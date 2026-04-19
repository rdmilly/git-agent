"""git-agent FastAPI service with mounted FastMCP surface.

Single-container architecture: FastAPI serves REST endpoints (/health, /git_commit,
etc.) AND the FastMCP protocol at /mcp, all from one process. Tool handlers are
plain async functions; both the REST routes and the @mcp.tool() decorators delegate
to the same handlers — no duplication, no HTTP hop between MCP and business logic.

This is lighter than the two-container helix-cortex + helix-mcp pattern. Helix splits
because it has many non-MCP REST consumers, multiple backends, and a heavy dependency
set. git-agent has three tools, one consumer (Claude via MCP), and no reason to pay
the split cost.

Endpoints:
    GET  /health           — liveness probe
    POST /git_commit       — REST: commit + push + open PR
    POST /git_init_repo    — REST: stub for Phase 1.5
    POST /git_new_project  — REST: stub for Phase 1.5
    *    /mcp/*             — FastMCP streamable-http transport (Provisioner talks here)
"""
from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from app.models import (
    CommitRequest,
    CommitResponse,
    CommitType,
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

VERSION = "0.2.0"

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
    logger.info("git-agent %s ready (single-container, /mcp mounted)", VERSION)
    yield
    logger.info("git-agent shutting down")


# --- FastMCP instance (mounted into FastAPI below) ---------------------------

mcp = FastMCP(
    "git_agent",
    stateless_http=True,
    instructions=(
        "git-agent owns structured git operations for Millyweb repos. Use git_commit "
        "to stage/commit/push/open-PR in one call; the commit message is generated "
        "from the diff by Haiku. Include session_uri for attribution. Use dry_run=true "
        "to preview the message before pushing."
    ),
)

app = FastAPI(title="git-agent", version=VERSION, lifespan=lifespan)


# --- Helpers ------------------------------------------------------------------

def _kebab(s: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return slug[:40].rstrip("-") or "change"


def _append_trailer(message: str, session_uri: Optional[str]) -> str:
    if not session_uri:
        return message
    trailer = f"Claude-Session: {session_uri}"
    lines = message.rstrip().splitlines()
    if lines and re.match(r"^[A-Za-z][A-Za-z0-9-]*: ", lines[-1]):
        return message.rstrip() + "\n" + trailer + "\n"
    return message.rstrip() + "\n\n" + trailer + "\n"


def _pr_body(commit_message: str, intent: str, session_uri: Optional[str]) -> str:
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


# --- Shared handlers (called by BOTH REST and MCP) ---------------------------

async def _handle_commit(req: CommitRequest) -> CommitResponse:
    """The actual commit pipeline. Returns a CommitResponse either way."""
    assert _git_ops is not None and _github_api is not None and _haiku is not None

    try:
        local = await _git_ops.ensure_clone(req.repo)

        if not await _git_ops.has_changes(local):
            return CommitResponse(
                status="error",
                repo=req.repo,
                commit_message="",
                error="no changes to commit",
                error_remediation="Make file changes in the repo first, or use dry_run=True to test generation.",
            )

        files_changed = await _git_ops.stage_all(local)
        diff = await _git_ops.staged_diff(local)

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

        branch = f"{req.type.value}/{_kebab(req.intent)}"
        await _git_ops.create_branch(local, branch)
        sha = await _git_ops.commit(local, message)
        await _git_ops.push(local, branch)

        subject = message.split("\n", 1)[0]
        pr = await _github_api.open_pull_request(
            repo=req.repo,
            head_branch=branch,
            base_branch=req.merge_target,
            title=subject,
            body=_pr_body(message, req.intent, req.session_uri),
        )

        if AUTO_MERGE_DEFAULT:
            try:
                await _github_api.enable_auto_merge(req.repo, pr.number, method="squash")
            except GitHubError as e:
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
            status="error", repo=req.repo, commit_message="",
            error=f"commit message generation failed: {e}",
            error_remediation="Verify OPENROUTER_API_KEY / ANTHROPIC_API_KEY are set and valid. Check git-agent container logs for upstream LLM errors.",
        )
    except GitOpsError as e:
        return CommitResponse(
            status="error", repo=req.repo, commit_message="",
            error=f"git operation failed: {e}",
            error_remediation="Check GITHUB_TOKEN scopes (needs repo+workflow). Verify the repo exists and your token can access it.",
        )
    except GitHubError as e:
        return CommitResponse(
            status="error", repo=req.repo, commit_message="",
            error=f"GitHub API failed: {e}",
            error_remediation="Check `gh auth status` in the container. PR creation requires the head branch to be pushed successfully first.",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("unexpected failure in commit pipeline")
        return CommitResponse(
            status="error", repo=req.repo, commit_message="",
            error=f"unexpected error: {type(e).__name__}: {e}",
            error_remediation="Check git-agent container logs for full stack trace.",
        )


# --- REST endpoints (for curl/debug/humans) ----------------------------------

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
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
async def rest_git_commit(req: CommitRequest) -> CommitResponse:
    return await _handle_commit(req)


@app.post("/git_init_repo", response_model=InitRepoResponse)
async def rest_git_init_repo(req: InitRepoRequest) -> InitRepoResponse:
    return InitRepoResponse(
        status="not_implemented",
        repo=req.repo,
        branch_protection_enabled=False,
        files_installed=[],
        error="git_init_repo is a Phase 1.5 stub. Tool surface is stable; implementation lands once the agent is dogfooded against paving-agent.",
    )


@app.post("/git_new_project", response_model=NewProjectResponse)
async def rest_git_new_project(req: NewProjectRequest) -> NewProjectResponse:
    return NewProjectResponse(
        status="not_implemented",
        error="git_new_project is a Phase 1.5 stub. Commit pipeline ships first; scaffolding next.",
    )


# --- MCP tool surface (same handlers, MCP-decorated) -------------------------

@mcp.tool()
async def git_commit(
    repo: str,
    intent: str,
    type: str,
    merge_target: str = "main",
    session_uri: str = "",
    dry_run: bool = False,
) -> str:
    """Stage, commit, push, and open a PR on a Millyweb repo.

    The commit message is generated from the diff by Haiku; `intent` is a
    short hint for the subject, not the authoritative description. Every
    Claude-originated commit should go through this tool rather than raw git.

    Args:
        repo: Slug like "paving-agent" (→ github.com/rdmilly/paving-agent) or full URL.
        intent: Short free-text (3-200 chars) describing what the change is for.
        type: One of feat, fix, refactor, docs, chore, test, perf, ci.
        merge_target: Base branch for the PR (default: main).
        session_uri: Claude session URI for the commit trailer; recommended.
        dry_run: If True, generate the message but do not push or open a PR.

    Returns:
        JSON string with status, repo, branch, commit_sha, commit_message, pr_url,
        pr_number, files_changed. On error: status="error" with error + remediation.
    """
    import json
    req = CommitRequest(
        repo=repo,
        intent=intent,
        type=CommitType(type),
        merge_target=merge_target,
        session_uri=session_uri or None,
        dry_run=dry_run,
    )
    resp = await _handle_commit(req)
    return json.dumps(resp.model_dump(exclude_none=False), indent=2)


@mcp.tool()
async def git_init_repo(
    repo: str,
    enable_branch_protection: bool = False,
    required_ci_check: str = "ci",
) -> str:
    """Install CI workflow, PR template, and optionally enable branch protection.

    Currently stubbed (returns not_implemented). Tool surface is stable; the
    underlying GitHub API wiring lands in a Phase 1.5 PR.
    """
    import json
    req = InitRepoRequest(
        repo=repo,
        enable_branch_protection=enable_branch_protection,
        required_ci_check=required_ci_check,
    )
    resp = await rest_git_init_repo(req)
    return json.dumps(resp.model_dump(exclude_none=False), indent=2)


@mcp.tool()
async def git_new_project(
    kb_path: str,
    repo_slug: str = "",
    private: bool = False,
) -> str:
    """Scaffold a new repo from a KB PRD with `type: new-project` frontmatter.

    Currently stubbed. Will create the repo, scaffold README/LICENSE/CI/PR template,
    register a placeholder dashboard, then stop before code — first real commit
    defines the code.
    """
    import json
    req = NewProjectRequest(kb_path=kb_path, repo_slug=repo_slug or None, private=private)
    resp = await rest_git_new_project(req)
    return json.dumps(resp.model_dump(exclude_none=False), indent=2)


@mcp.tool()
async def git_health() -> str:
    """Return health + config visibility for the git-agent backend."""
    import json
    return json.dumps((await health()).model_dump(), indent=2)


# --- Mount MCP onto FastAPI at /mcp ------------------------------------------

# streamable_http_app() returns an ASGI app that speaks MCP protocol.
# Provisioner configures transport="streamable-http" endpoint="/mcp" against us.
app.mount("/mcp", mcp.streamable_http_app())
