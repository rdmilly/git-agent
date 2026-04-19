"""Git primitives: clone, pull, branch, commit, push.

Thin wrapper over the `git` binary.  We shell out rather than use a library
(e.g., GitPython) because:
  1. The `git` binary is the ground truth — library abstractions lag.
  2. Our operations are simple; we don't need object-model access.
  3. Errors from `git` itself are more actionable than library translations.

All operations are repo-scoped to a cache directory at CACHE_ROOT/<slug>.
Shallow clones by default (depth=20) to keep disk use bounded.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default clone depth — enough to read recent history for diffs, not so much
# that we balloon disk usage for every repo we touch.
DEFAULT_CLONE_DEPTH = 20


@dataclass
class GitResult:
    """Result of a git command execution.

    `ok` reflects exit code 0.  `stdout` and `stderr` preserve raw output
    for error messages; we never parse git output to infer success/failure
    when a clean exit code is available.
    """

    ok: bool
    stdout: str
    stderr: str
    returncode: int
    command: str


class GitOpsError(Exception):
    """Raised when a git operation fails in a way the caller must handle.

    The exception's string form is safe to return to an MCP caller — it
    redacts known sensitive patterns (URLs with tokens, env var contents).
    """

    def __init__(self, message: str, result: Optional[GitResult] = None):
        super().__init__(_redact(message))
        self.result = result
        self.remediation: Optional[str] = None


# Patterns that might contain secrets in git command output.  We replace
# matches with [REDACTED] before returning anything to the caller.
_SECRET_PATTERNS = [
    # HTTPS URLs with basic-auth tokens: https://TOKEN@github.com/...
    re.compile(r"https://[^:]+:[^@]+@"),
    re.compile(r"https://[A-Za-z0-9_]{20,}@"),
    # GitHub tokens (ghp_, gho_, ghs_, ghu_, ghr_ prefixes + 36 chars)
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    # Generic high-entropy strings that look like tokens (conservative)
    re.compile(r"[A-Za-z0-9_\-]{40,}=+"),
]


def _redact(text: str) -> str:
    """Scrub known secret patterns from text before returning to caller."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


class GitOps:
    """Git operations against a local repo cache.

    Constructor takes the cache root and the GitHub token used for push auth.
    All public methods are async — we run git as subprocess via asyncio so the
    FastAPI event loop isn't blocked during slow operations (push, fetch).
    """

    def __init__(self, cache_root: Path, github_token: str, github_user: str = "git"):
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._token = github_token
        self._user = github_user

    def _auth_url(self, slug_or_url: str) -> str:
        """Convert a slug or URL into an https URL with the token embedded.

        Accepts:
          - 'paving-agent'                     → github.com/rdmilly/paving-agent
          - 'rdmilly/paving-agent'             → github.com/rdmilly/paving-agent
          - 'https://github.com/rdmilly/foo'   → passed through (token injected)
        """
        if slug_or_url.startswith("http"):
            # Strip any existing auth prefix, then inject our token.
            bare = re.sub(r"^https://[^@]*@", "https://", slug_or_url)
            return bare.replace("https://", f"https://{self._user}:{self._token}@")
        if "/" in slug_or_url:
            owner, name = slug_or_url.split("/", 1)
        else:
            owner, name = "rdmilly", slug_or_url
        return f"https://{self._user}:{self._token}@github.com/{owner}/{name}.git"

    def repo_path(self, slug_or_url: str) -> Path:
        """Return the local cache directory for a repo."""
        if slug_or_url.startswith("http"):
            # Derive slug from URL: strip .git, take last two path segments.
            bare = slug_or_url.rstrip("/").removesuffix(".git")
            parts = bare.split("/")[-2:]
            slug = "-".join(parts)
        elif "/" in slug_or_url:
            slug = slug_or_url.replace("/", "-")
        else:
            slug = slug_or_url
        return self.cache_root / slug

    async def _run(
        self,
        *args: str,
        cwd: Optional[Path] = None,
        check: bool = True,
    ) -> GitResult:
        """Run a git command and return the result.

        Logs command at DEBUG with the token elided.  Raises GitOpsError
        on non-zero exit if check=True.
        """
        cmd_display = " ".join(shlex.quote(a) for a in args)
        cmd_display = _redact(cmd_display)
        logger.debug("git: %s (cwd=%s)", cmd_display, cwd)

        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        ok = proc.returncode == 0

        result = GitResult(
            ok=ok,
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode or -1,
            command=cmd_display,
        )

        if check and not ok:
            raise GitOpsError(
                f"git {args[0] if args else '?'} failed: {stderr.strip() or stdout.strip()}",
                result=result,
            )
        return result

    async def ensure_clone(self, slug_or_url: str) -> Path:
        """Clone the repo if absent, else fetch+reset to latest origin/<default>.

        Returns the local path.  This is the single entry point for "make sure
        we have a fresh copy" — all other methods assume it's been called.
        """
        local = self.repo_path(slug_or_url)
        auth_url = self._auth_url(slug_or_url)

        if (local / ".git").exists():
            # Existing clone: fetch and fast-forward.
            await self._run("fetch", "--all", "--prune", cwd=local)
            default = await self._default_branch(local)
            await self._run("checkout", default, cwd=local)
            await self._run("reset", "--hard", f"origin/{default}", cwd=local)
            return local

        local.parent.mkdir(parents=True, exist_ok=True)
        await self._run(
            "clone",
            "--depth",
            str(DEFAULT_CLONE_DEPTH),
            auth_url,
            str(local),
        )
        return local

    async def _default_branch(self, repo: Path) -> str:
        """Return the upstream default branch name (usually 'main')."""
        result = await self._run(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            cwd=repo,
            check=False,
        )
        if result.ok:
            # Output is like: refs/remotes/origin/main
            return result.stdout.strip().rsplit("/", 1)[-1]
        # Fallback: guess 'main', then 'master'.
        for guess in ("main", "master"):
            probe = await self._run(
                "rev-parse", "--verify", f"origin/{guess}", cwd=repo, check=False,
            )
            if probe.ok:
                return guess
        raise GitOpsError("could not determine default branch")

    async def has_changes(self, repo: Path) -> bool:
        """True if the working tree has staged or unstaged changes."""
        result = await self._run("status", "--porcelain", cwd=repo)
        return bool(result.stdout.strip())

    async def stage_all(self, repo: Path) -> list[str]:
        """Stage every change and return the list of affected paths."""
        await self._run("add", "-A", cwd=repo)
        result = await self._run("diff", "--cached", "--name-only", cwd=repo)
        return [line for line in result.stdout.splitlines() if line.strip()]

    async def staged_diff(self, repo: Path, max_chars: int = 20000) -> str:
        """Return the staged diff, truncated to max_chars for LLM context."""
        result = await self._run("diff", "--cached", "--no-color", cwd=repo)
        diff = result.stdout
        if len(diff) > max_chars:
            diff = diff[:max_chars] + f"\n\n[... truncated; diff was {len(result.stdout)} chars]"
        return diff

    async def create_branch(self, repo: Path, branch: str) -> None:
        """Create and check out a new branch from current HEAD."""
        # -B = create or reset — safe if this exact branch already exists locally.
        await self._run("checkout", "-B", branch, cwd=repo)

    async def commit(
        self,
        repo: Path,
        message: str,
        author_name: str = "Claude (git-agent)",
        author_email: str = "claude@millyweb.internal",
    ) -> str:
        """Commit staged changes with the given message, return the new SHA."""
        env_args = [
            "-c", f"user.name={author_name}",
            "-c", f"user.email={author_email}",
            "commit", "-m", message,
        ]
        await self._run(*env_args, cwd=repo)
        sha_result = await self._run("rev-parse", "HEAD", cwd=repo)
        return sha_result.stdout.strip()

    async def push(self, repo: Path, branch: str, set_upstream: bool = True) -> None:
        """Push branch to origin.

        The remote URL already contains auth from ensure_clone, so no
        additional credential handling is needed here.
        """
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.extend(["origin", branch])
        await self._run(*args, cwd=repo)
