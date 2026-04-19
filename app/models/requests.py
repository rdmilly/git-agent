"""Pydantic models for the git-agent HTTP API.

These define the public contract between the MCP wrapper and the service.
Changes here require coordinated updates to the Provisioner tool schema.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CommitType(str, Enum):
    """Conventional Commit types we accept.

    Constrained on purpose — the closed set forces callers to categorize
    their intent, which improves downstream STATUS.md generation.
    """

    feat = "feat"
    fix = "fix"
    refactor = "refactor"
    docs = "docs"
    chore = "chore"
    test = "test"
    perf = "perf"
    ci = "ci"


class CommitRequest(BaseModel):
    """Input to POST /git_commit.

    `repo` is a slug like "paving-agent" (implicitly github.com/rdmilly/<slug>)
    or a full URL for external repos.  `intent` is free-text describing what
    the change is FOR — the agent generates the message FROM the diff, not
    from intent, so be brief here.
    """

    repo: str = Field(
        ...,
        description="Repo slug (e.g. 'paving-agent') or full https URL",
        min_length=1,
    )
    intent: str = Field(
        ...,
        description="Short free-text describing what the change is for",
        min_length=3,
        max_length=200,
    )
    type: CommitType = Field(
        ...,
        description="Conventional Commit type",
    )
    merge_target: str = Field(
        default="main",
        description="Branch to PR against; defaults to main",
    )
    session_uri: Optional[str] = Field(
        default=None,
        description="Claude session URI for the commit trailer",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, generate the commit message but don't push or open PR",
    )


class CommitResponse(BaseModel):
    """Output from POST /git_commit.

    On dry_run=True, `pr_url` is None and `commit_sha` is None — only the
    generated `commit_message` is populated so the caller can sanity-check.
    """

    status: str = Field(..., description="'success', 'dry_run', or 'error'")
    repo: str
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    commit_message: str = Field(..., description="The generated Conventional Commit message")
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    files_changed: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    error_remediation: Optional[str] = Field(
        default=None,
        description="Human-readable guidance on how to resolve the error",
    )


class InitRepoRequest(BaseModel):
    """Input to POST /git_init_repo.

    Applies branch protection, CI workflow, PR template, and .pa.yml config
    to an existing repo.  Idempotent — safe to call multiple times.

    `enable_branch_protection` is separated from the rest because per our
    dogfood decision (PRD Q2), we want to install the workflow files before
    flipping protection.
    """

    repo: str = Field(..., description="Repo slug or full URL")
    enable_branch_protection: bool = Field(
        default=False,
        description="If True, flip on GitHub branch protection. Leave False during dogfood.",
    )
    require_ci_check: Optional[str] = Field(
        default="ci",
        description="Name of the required CI check (must match workflow job id)",
    )


class InitRepoResponse(BaseModel):
    status: str
    repo: str
    branch_protection_enabled: bool = False
    files_installed: list[str] = Field(default_factory=list)
    pr_url: Optional[str] = Field(
        default=None,
        description="PR opened with workflow files if repo needed them",
    )
    error: Optional[str] = None


class NewProjectRequest(BaseModel):
    """Input to POST /git_new_project.

    Reads a PRD from the Helix KB, creates the repo, scaffolds it.  The PRD
    MUST have frontmatter with `type: new-project` or this request is rejected.
    """

    kb_path: str = Field(
        ...,
        description="Path to the PRD in the Helix KB, e.g. 'projects/git-agent/PRD.md'",
    )
    repo_slug: Optional[str] = Field(
        default=None,
        description="Override the slug derived from PRD frontmatter 'project' field",
    )
    private: bool = Field(
        default=False,
        description="Create repo as private. Default is public (Option B requires public images, not public source — but we default to public for consistency).",
    )


class NewProjectResponse(BaseModel):
    status: str
    repo_url: Optional[str] = None
    dashboard_url: Optional[str] = None
    files_scaffolded: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    github_token_present: bool
    openrouter_key_present: bool
    cache_dir_writable: bool
