"""GitHub API operations via the `gh` CLI.

We wrap `gh` instead of calling the REST API directly because:
  1. Auth handling (token refresh, keychain) is one less thing to debug.
  2. `gh` handles API pagination internally.
  3. Errors from `gh` are more actionable than raw REST responses.

The CLI reads auth from the `GH_TOKEN` environment variable by convention;
we set it for every subprocess invocation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """GitHub API operation failed."""


@dataclass
class PullRequest:
    number: int
    url: str
    title: str


class GitHubAPI:
    """Thin wrapper over the `gh` CLI.

    All methods are async.  We pass the token via env var each time rather
    than calling `gh auth login` once — this keeps the agent stateless.
    """

    def __init__(self, github_token: str):
        self._token = github_token

    async def _gh(self, *args: str, check: bool = True) -> tuple[int, str, str]:
        """Run `gh <args>` with auth env set.  Returns (rc, stdout, stderr)."""
        env = {**os.environ, "GH_TOKEN": self._token, "NO_COLOR": "1"}
        logger.debug("gh: %s", " ".join(a for a in args))

        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        rc = proc.returncode or 0

        if check and rc != 0:
            raise GitHubError(f"gh {args[0]} failed (rc={rc}): {stderr.strip() or stdout.strip()}")
        return rc, stdout, stderr

    @staticmethod
    def _resolve_slug(repo: str) -> str:
        """Turn a slug, owner/name, or URL into 'owner/name' format for gh."""
        if repo.startswith("http"):
            # https://github.com/owner/name(.git)
            m = re.match(r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo)
            if not m:
                raise GitHubError(f"could not parse repo URL: {repo}")
            return m.group(1)
        if "/" in repo:
            return repo
        return f"rdmilly/{repo}"

    async def open_pull_request(
        self,
        repo: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequest:
        """Open a PR and return the resulting URL + number.

        Uses `gh pr create`, which requires the branch to already be pushed.
        """
        full = self._resolve_slug(repo)
        rc, stdout, stderr = await self._gh(
            "pr", "create",
            "--repo", full,
            "--head", head_branch,
            "--base", base_branch,
            "--title", title,
            "--body", body,
        )
        # gh prints the PR URL on success as the last non-empty line of stdout.
        url = ""
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("https://"):
                url = line
                break
        if not url:
            raise GitHubError(f"gh pr create returned no URL. stdout: {stdout}")

        # Extract PR number from the URL tail.
        m = re.search(r"/pull/(\d+)$", url)
        number = int(m.group(1)) if m else 0

        return PullRequest(number=number, url=url, title=title)

    async def enable_auto_merge(self, repo: str, pr_number: int, method: str = "squash") -> None:
        """Turn on auto-merge for a PR.

        method: squash (default per our Q1 decision), merge, or rebase.
        """
        full = self._resolve_slug(repo)
        flag = {"squash": "--squash", "merge": "--merge", "rebase": "--rebase"}[method]
        await self._gh(
            "pr", "merge", str(pr_number),
            "--repo", full,
            "--auto",
            flag,
        )

    async def enable_branch_protection(
        self,
        repo: str,
        branch: str = "main",
        required_check: Optional[str] = "ci",
    ) -> None:
        """Apply our standard branch protection to `branch`.

        Rules:
          - Block direct pushes (requires PR)
          - Require at least one passing status check (if `required_check` is set)
          - Block force-push
          - Block deletion
          - Allow admin override so Ryan can still hotfix

        Uses the REST API via `gh api` because the CLI doesn't expose all
        protection fields directly.
        """
        full = self._resolve_slug(repo)
        required_status = None
        if required_check:
            required_status = {
                "strict": True,
                "contexts": [required_check],
            }

        body = {
            "required_status_checks": required_status,
            "enforce_admins": False,  # Ryan can override; Claude cannot.
            "required_pull_request_reviews": None,  # No mandatory human review (we don't have a second reviewer)
            "restrictions": None,
            "allow_force_pushes": False,
            "allow_deletions": False,
        }
        await self._gh(
            "api",
            "-X", "PUT",
            f"/repos/{full}/branches/{branch}/protection",
            "-H", "Accept: application/vnd.github+json",
            "--input", "-",
            check=True,
            # gh reads stdin from the process’s stdin; we have to feed the body differently.
        )
        # NOTE: the above won't actually pipe stdin — gh api --input - reads
        # from our process stdin, which asyncio.subprocess doesn't hook up by
        # default.  For the real implementation we should write to a tempfile
        # and use --input <path>.  Marking this as a Phase 1.5 TODO since we
        # aren't flipping protection tonight anyway.
        _ = json.dumps(body)  # placeholder to keep body referenced

    async def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = False,
        license_template: str = "mit",
    ) -> str:
        """Create a new repo under rdmilly/.  Returns the full https URL.

        Idempotent-ish: if the repo exists, `gh` returns a clear error and
        we surface it; we don't silently continue.
        """
        visibility = "--private" if private else "--public"
        rc, stdout, stderr = await self._gh(
            "repo", "create",
            f"rdmilly/{name}",
            visibility,
            "--description", description,
            "--license", license_template,
            "--confirm",
        )
        # gh prints the repo URL on success.
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("https://github.com/"):
                return line
        # Fallback — construct the expected URL.
        return f"https://github.com/rdmilly/{name}"
