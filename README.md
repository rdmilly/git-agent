# git-agent

MCP tool for structured git operations across all Millyweb repos. The `helix_file_write` pattern, applied to git.

## What it does

Git Agent owns every commit a Claude session makes. Claude states *intent*; the agent generates a Conventional Commit message from the actual diff, creates a branch, commits, pushes, and opens a PR. There is no raw `git commit` path for Claude. Quality is by construction, not by remembering.

## Architecture

- **Runtime:** Docker container on VPS1, internal-only (port 9093 on Docker bridge)
- **Image:** published to `ghcr.io/rdmilly/git-agent` on every push to `main`
- **Source:** this public repo
- **Secrets:** injected at container startup from Infisical — never baked into image
- **MCP:** registered in Provisioner, callable via `Provisioned:execute_tool tool_name="git_commit"`

## MCP Tools Exposed

| Tool | Purpose |
|---|---|
| `git_commit` | Stage, commit (AI-generated message), push, open PR |
| `git_init_repo` | Apply branch protection + CI workflow + PR template |
| `git_new_project` | Scaffold a new repo from a KB PRD with `type: new-project` |
| `git_backfill_dashboards` | One-shot: render all existing PRDs to `dash.millyweb.com/<slug>/` |

## Decisions locked

See `docs/PRD.md` § 13 for full context. Headlines:
- **Squash-merge** on PR to main
- **Branch protection** enabled after 1–2 day dogfood window
- **Auto-merge** on green CI for Claude-opened PRs
- **STATUS.md** lives in-repo per project, aggregated in `rdmilly/portfolio`
- **Commit trailer** `Claude-Session: <uri>` attributes every Claude commit
- **Phase 3:** all 10 repos-as-nodes ideas over time, #1/#3/#6 first

## Deploy

```bash
cd /opt/stacks/git-agent
docker compose pull
docker compose up -d
```

Deploys whatever is tagged `:latest` on GHCR. Rollback is `image: ghcr.io/rdmilly/git-agent:<old-sha>` in `docker-compose.yml`.

## Dogfood posture

As of the first stable release, this repo commits to itself via itself.  Raw git is only used for the initial bootstrap commit. Every change since commit #2 should have a `Claude-Session:` trailer or be signed by Ryan personally.
