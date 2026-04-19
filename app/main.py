"""git-agent FastAPI service.

Runs on port 9093 inside a Docker container on VPS1.  Exposes HTTP endpoints
that the Provisioner MCP wrapper routes tool calls into.

Endpoints:
    GET  /health           — liveness probe
    POST /git_commit       — commit + push + open PR
    POST /git_init_repo    — install CI/PR template, optionally enable protection
    POST /git_new_project  — scaffold new repo from KB PRD (Phase 1.5)

Design:
  - Services (GitOps, GitHubAPI, Haiku) are constructed once at startup and
    reused across requests.  Tokens are read from env at startup.
  - Every endpoint catches its tool-specific exception class and returns a
    structured error response with remediation text, so callers can diagnose
    without log-diving.
"""
from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException

from app.models import (
    CommitRequest,
    CommitResponse,
    HealthResponse,
    InitRepoRequest,
    InitRepoResponse,
    NewProjectRequest,
    NewProjectResponse,
)
from app.services import (
    GitHubAPI,
    GitHubError,
    GitOps,
    GitOpsError,
    Haiku,
    HaikuError,
)

VERSION = "0.1.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("git-agent")


# --- Settings, read from env at startup ---------------------------------------

CACHE_ROOT = Path(os.environ.get("GIT_AGENT_CACHE_ROOT", "/opt/data/git-agent/cache"))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AUTO_MERGE_DEFAULT = os.environ.get("GIT_AGENT_AUTO_MERGE", "true").lower() == "true"

# Lazily-initialized service singletons (populated in lifespan).
_git_ops: Optional[GitOps] = None
_github_api: Optional[GitHubAPI] = None
_haiku: Optional[Haiku] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Construct the service singletons at startup."""
    global _git_ops, _github_api, _haiku
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN is empty; git operations will fail")
    _git_ops = GitOps(cache_root=CACHE_ROOT, github_token=GITHUB_TOKEN or "placeholder")
    _github_api = GitHubAPI(github_token=GITHUB_TOKEN or "placeholder")
    _haiku = Haiku(openrouter_key=OPENROUTER_KEY, anthropic_key=ANTHROPIC_KEY)
    logger.info("git-agent %s ready", VERSION)
    yield
    logger.info("git-agent shutting down")


app = FastAPI(title="git-agent", version=VERSION, lifespan=lifespan)


# --- Helpers ------------------------------------------------------------------

def _kebab(s: str) -> str:
    """Convert an intent string to a kebab-case branch slug.

    "wire intake dialogue into main" -> "wire-intake-dialogue-into-main"
    Caps length at 40 chars to keep branch names readable.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return slug[:40].rstrip("-") or "change"


def _append_trailer(message: str, session_uri: Optional[str]) -> str:
    """Append the Claude-Session trailer if session_uri is provided.

    Per our Q5 decision, commit attribution lives in a trailer.  Trailers
    follow RFC-ish convention: `Key: Value`, separated from body by blank line.
    """
    if not session_uri:
        return message
    trailer = f"Claude-Session: {session_uri}"
    # If message already ends with trailers, append; else add blank line first.
    lines = message.rstrip().splitlines()
    # Detect a trailer block: last block is all `Key: Value` lines.
    if lines and re.match(r"^[A-Za-z][A-Za-z0-9-]*: ", lines[-1]):
        return message.rstrip() + "\n" + trailer + "\n"
    return message.rstrip() + "\n\n" + trailer + "\n"


def _pr_body(commit_message: str, intent: str, session_uri: Optional[str]) -> str:
    """Build a PR body from the commit message + metadata."""
    parts = [
        "## Summary",
        "",
        commit_message.split("\n", 1)[1].strip() if "\n" in commit_message else "(no body)",
        "",
        "## Intent",
        "",
        intent,
        "",
    ]
    if session_uri:
        parts += [
            "## Provenance",
            "",
            f"Opened by Claude session: `{session_uri}`",
            "",
        ]
    parts += [
        "---",
        "*This PR was opened by `git-agent`. The commit message was generated from the diff by Haiku; the intent above is the author's hint, not the authoritative description of changes.*",
    ]
    return "\n".join(parts)


# --- Endpoints ----------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness + configuration visibility."""
    try:
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        cache_writable = os.access(CACHE_ROOT, os.W_OK)
    except Exception:
        cache_writable = False
    return HealthResponse(
        status="ok",
        version=VERSION,
        github_token_present=bool(GITHUB_TOKEN),
        openrouter_key_present=bool(OPENROUTER_KEY),
        cache_dir_writable=cache_writable,
    )


@app.post("/git_commit", response_model=CommitResponse)
async def git_commit(req: CommitRequest) -> CommitResponse:
    """Stage, commit (AI-generated message), push, open PR.

    On dry_run=True, generates the message and returns it without pushing.
    On error, returns a CommitResponse with status='error' and remediation.
    """
    assert _git_ops is not None and _github_api is not None and _haiku is not None

    try:
        # 1. Ensure local clone is current.
        local = await _git_ops.ensure_clone(req.repo)

        # 2. Anything to commit?
        if not await _git_ops.has_changes(local):
            return CommitResponse(
                status="error",
                repo=req.repo,
                commit_message="",
                error="no changes to commit",
                error_remediation="Make file changes in the repo first, or use dry_run=True to test generation.",
            )

        # 3. Stage and read diff.
        files_changed = await _git_ops.stage_all(local)
        diff = await _git_ops.staged_diff(local)

        # 4. Generate commit message from diff.
        message = await _haiku.generate_commit_message(
            commit_type=req.type.value,
            intent=req.intent,
            diff=diff,
        )
        message = _append_trailer(message, req.session_uri)

        if req.dry_run:
            return CommitResponse(
                status="dry_run",
                repo=req.repo,
                commit_message=message,
                files_changed=files_changed,
            )

        # 5. Branch, commit, push.
        branch = f"{req.type.value}/{_kebab(req.intent)}"
        await _git_ops.create_branch(local, branch)
        sha = await _git_ops.commit(local, message)
        await _git_ops.push(local, branch)

        # 6. Open PR.
        subject = message.split("\n", 1)[0]
        pr = await _github_api.open_pull_request(
            repo=req.repo,
            head_branch=branch,
            base_branch=req.merge_target,
            title=subject,
            body=_pr_body(message, req.intent, req.session_uri),
        )

        # 7. Enable auto-merge if configured.
        if AUTO_MERGE_DEFAULT:
            try:
                await _github_api.enable_auto_merge(req.repo, pr.number, method="squash")
            except GitHubError as e:
                # Auto-merge may fail if branch protection isn't set up — not fatal.
                logger.warning("auto-merge failed for %s#%d: %s", req.repo, pr.number, e)

        return CommitResponse(
            status="success",
            repo=req.repo,
            branch=branch,
            commit_sha=sha,
            commit_message=message,
            pr_url=pr.url,
            pr_number=pr.number,
            files_changed=files_changed,
        )

    except HaikuError as e:
        return CommitResponse(
            status="error",
            repo=req.repo,
            commit_message="",
            error=f"commit message generation failed: {e}",
            error_remediation="Verify OPENROUTER_API_KEY / ANTHROPIC_API_KEY are set and valid. Check git-agent container logs for upstream LLM errors.",
        )
    except GitOpsError as e:
        return CommitResponse(
            status="error",
            repo=req.repo,
            commit_message="",
            error=f"git operation failed: {e}",
            error_remediation="Check GITHUB_TOKEN scopes (needs repo+workflow). Verify the repo exists and your token can access it.",
        )
    except GitHubError as e:
        return CommitResponse(
            status="error",
            repo=req.repo,
            commit_message="",
            error=f"GitHub API failed: {e}",
            error_remediation="Check `gh auth status` in the container. PR creation requires the head branch to be pushed successfully first.",
        )
    except Exception as e:  # noqa: BLE001 — surface unexpected errors to caller
        logger.exception("unexpected failure in /git_commit")
        return CommitResponse(
            status="error",
            repo=req.repo,
            commit_message="",
            error=f"unexpected error: {type(e).__name__}: {e}",
            error_remediation="Check git-agent container logs for full stack trace.",
        )


@app.post("/git_init_repo", response_model=InitRepoResponse)
async def git_init_repo(req: InitRepoRequest) -> InitRepoResponse:
    """Install CI workflow, PR template, .pa.yml; optionally enable protection.

    Stub implementation in this first cut — we acknowledge the request and
    note that the underlying `enable_branch_protection` is a Phase 1.5 TODO
    because the stdin piping for `gh api` isn't wired yet.

    This endpoint exists now so the tool surface is stable; implementation
    catches up in a later PR against this same repo (which will be the first
    dogfood proof-point).
    """
    return InitRepoResponse(
        status="not_implemented",
        repo=req.repo,
        branch_protection_enabled=False,
        files_installed=[],
        error="git_init_repo is a Phase 1.5 stub. Capability exists but is not yet wired. Will be filled in once the agent is dogfooded against paving-agent.",
    )


@app.post("/git_new_project", response_model=NewProjectResponse)
async def git_new_project(req: NewProjectRequest) -> NewProjectResponse:
    """Scaffold a new project from a PRD with `type: new-project` frontmatter.

    Stub for the same reason as /git_init_repo — we're stabilizing the tool
    surface in Phase 1, and filling in scaffolding logic in Phase 1.5 once
    the commit pipeline is proven.
    """
    return NewProjectResponse(
        status="not_implemented",
        error="git_new_project is a Phase 1.5 stub. Commit pipeline ships first; scaffolding next.",
    )
