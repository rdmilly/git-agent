"""git-agent MCP server.

FastMCP-based tool surface. Proxies MCP tool calls over HTTP to the
git-agent REST service (app.main) running in the sibling container.

This is the same two-process pattern Helix uses: `helix-cortex` (REST) +
`helix-mcp` (FastMCP that proxies to cortex). Keeps the REST API clean
and makes the MCP protocol concerns separate from the git/github/haiku
business logic.

Architecture:
    Claude → Provisioner → git-agent-mcp (:9094, this file)
                              ↓ http://git-agent:9093 (same compose network)
                          git-agent REST (app.main)
"""
from __future__ import annotations

import json
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

AGENT_URL = os.environ.get("GIT_AGENT_URL", "http://git-agent:9093")
PORT = int(os.environ.get("PORT", "9094"))
TIMEOUT = 60.0

mcp = FastMCP("git_agent", host="0.0.0.0", port=PORT, stateless_http=True)


def _err(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return json.dumps({
            "error": f"HTTP {e.response.status_code}",
            "detail": e.response.text[:500],
        })
    if isinstance(e, httpx.TimeoutException):
        return json.dumps({"error": "git-agent request timed out"})
    return json.dumps({"error": f"{type(e).__name__}: {e}"})


async def _post(path: str, body: dict) -> str:
    """POST to the git-agent REST backend and return the JSON response as a string."""
    try:
        async with httpx.AsyncClient(base_url=AGENT_URL, timeout=TIMEOUT) as client:
            r = await client.post(path, json={k: v for k, v in body.items() if v is not None})
            r.raise_for_status()
            return json.dumps(r.json(), indent=2)
    except Exception as e:  # noqa: BLE001 — need to surface ANY error to the MCP caller
        return _err(e)


@mcp.tool()
async def git_commit(
    repo: str,
    intent: str,
    type: str,
    merge_target: Optional[str] = None,
    session_uri: Optional[str] = None,
    dry_run: Optional[bool] = None,
) -> str:
    """Stage, commit, push, and open a PR on a Millyweb repo.

    The commit message is generated from the diff by Haiku — intent is just
    a hint for the subject. Every Claude-originated commit should go through
    this tool; direct `git commit` / `git push origin main` is discouraged.

    Args:
        repo: Slug like "paving-agent" (implies github.com/rdmilly/<slug>)
              or a full https://github.com/... URL.
        intent: Short free-text (3-200 chars) describing what the change is for.
        type: One of feat, fix, refactor, docs, chore, test, perf, ci.
        merge_target: Base branch for PR (default: main).
        session_uri: Claude session URI for the commit trailer
                     (omit to skip attribution; recommended to include).
        dry_run: If True, generate the commit message but don't push or open PR.
                 Useful for previewing what Haiku will produce.

    Returns:
        JSON string with status, repo, branch, commit_sha, commit_message, pr_url,
        pr_number, files_changed. On error: status="error" with error + remediation.
    """
    return await _post(
        "/git_commit",
        {
            "repo": repo,
            "intent": intent,
            "type": type,
            "merge_target": merge_target,
            "session_uri": session_uri,
            "dry_run": dry_run,
        },
    )


@mcp.tool()
async def git_init_repo(
    repo: str,
    enable_branch_protection: Optional[bool] = None,
    required_ci_check: Optional[str] = None,
) -> str:
    """Install CI workflow, PR template, and optionally enable branch protection.

    Currently stubbed (returns not_implemented) — tool surface is stable but
    the underlying GitHub API wiring lands in a Phase 1.5 PR.

    Args:
        repo: Repo slug or full URL.
        enable_branch_protection: If True, flips branch protection on main.
                                  Leave False during the 1-2 day dogfood window.
        required_ci_check: Name of the required CI check (default: "ci").

    Returns:
        JSON string with status, repo, branch_protection_enabled, files_installed.
    """
    return await _post(
        "/git_init_repo",
        {
            "repo": repo,
            "enable_branch_protection": enable_branch_protection,
            "required_ci_check": required_ci_check,
        },
    )


@mcp.tool()
async def git_new_project(
    kb_path: str,
    repo_slug: Optional[str] = None,
    private: Optional[bool] = None,
) -> str:
    """Scaffold a new repo from a KB PRD with `type: new-project` frontmatter.

    Currently stubbed (returns not_implemented). When implemented, reads the
    PRD, creates the repo, scaffolds README/LICENSE/CI/PR template/.pa.yml,
    registers an empty dashboard entry, and emits a project.scaffolded event.
    Stops before writing any application code — that's the first real commit's job.

    Args:
        kb_path: Path to the PRD in the Helix KB, e.g. "projects/<x>/PRD.md".
        repo_slug: Override the slug derived from PRD frontmatter 'project' field.
        private: Create repo as private (default: False — public by convention).

    Returns:
        JSON string with status, repo_url, dashboard_url, files_scaffolded.
    """
    return await _post(
        "/git_new_project",
        {
            "kb_path": kb_path,
            "repo_slug": repo_slug,
            "private": private,
        },
    )


@mcp.tool()
async def git_health() -> str:
    """Return health + config visibility for the git-agent backend.

    Confirms token presence, LLM provider keys, and cache writability.
    Use this for debugging if git_commit returns cryptic errors.
    """
    try:
        async with httpx.AsyncClient(base_url=AGENT_URL, timeout=5.0) as client:
            r = await client.get("/health")
            r.raise_for_status()
            return json.dumps(r.json(), indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


if __name__ == "__main__":
    # Run as streamable-http MCP server (same transport as helix-mcp).
    mcp.run(transport="streamable-http")
