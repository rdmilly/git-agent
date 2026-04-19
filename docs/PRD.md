# PRD: Git Agent

**Product:** `git-agent` — MCP tool for structured git operations across all Millyweb repos
**Version:** 0.1 (draft)
**Author:** Ryan Milly + Claude
**Date:** 2026-04-19
**Status:** Draft — Phase 1 ready to build pending Open Questions

---

## 1. Problem Statement

Millyweb currently has ~20 repos under `github.com/rdmilly` with zero commit-hygiene enforcement and no workflow consistency across them.

- Every commit goes straight to `main` with a handwritten message
- No Pull Request discipline
- No CI gate on merge
- No Conventional Commits format
- No branch protection on any repo
- Claude sessions drift on "use `pa` wrapper" because discretion-based enforcement always erodes
- Ryan loses visibility into what got committed overnight; has to read raw git logs
- New project state isn't reflected in KB or dashboards until manually updated — often 9+ days stale

We already proved this exact problem was solvable for file writes: `helix_file_write` is the mandatory write surface, every file goes through the unified pipeline, nothing drifts. The Git Agent is `helix_file_write` for git.

---

## 2. Goals

- **Structural enforcement:** the MCP tool is the only git write path Claude sees. No raw `git commit` / `git push origin main` in Claude sessions.
- **Provisioner-wide:** single tool, works against any repo accessible to the `GITHUB_TOKEN` in Infisical. New repos inherit the workflow automatically.
- **Quality-by-construction:** commit messages generated from the actual diff by Haiku, not by Claude asserting what changed.
- **Proper PR workflow:** every change on a feature branch, PR opened, CI gate, squash-merge to main.
- **STATUS.md maintenance (Phase 2):** every meaningful commit updates the project's STATUS.md in the same operation.
- **Portfolio visibility (Phase 2):** a meta-repo aggregates every project's STATUS.md into a single never-stale index.
- **Zero human toil for commit messages:** Claude states intent, the agent does the rest.

## 3. Non-Goals

- Not a replacement for raw git at Ryan's terminal. Ryan can still `git` directly when needed. This tool is for Claude + for cross-session discipline.
- Not a CI system. We use GitHub Actions for CI; the agent calls it and reads results.
- Not a code review substitute. PRs exist to be reviewed; the agent opens them, humans approve.
- Not a repo manager — doesn't create repos, manage access, or handle secrets.
- Not autonomous merging by default — auto-merge on green CI is opt-in per repo.
- No direct-to-main path, ever. Even for "trivial" commits. Enforcement is absolute.

---

## 4. User Stories

### Phase 1 — Commit pipeline

**US-1 — Claude commits via one tool call**
As Claude, I finish editing code for a feature. I call `git_commit(repo="paving-agent", intent="wire intake dialogue into main", type="feat")`. The tool stages changes, generates a Conventional Commit message from the diff, creates branch `feat/wire-intake-dialogue`, commits, pushes, opens a PR with a filled template, returns the PR URL. I never touch raw git.

**US-2 — Ryan reviews the PR**
As Ryan, I wake up and see a PR waiting in `paving-agent`. The PR title and body were generated from the actual diff, not from Claude's assertion. I see what changed, what it's linked to, and CI status. I click Merge (squash) or reject with feedback.

**US-3 — Main is protected**
As Ryan or as Claude, if I try `git push origin main`, GitHub returns an error. The only path to main is through a PR. The rule is enforced by the server, not by a wrapper I might forget to use.

**US-4 — Commits carry session provenance**
As Ryan, I can see on any commit which Claude session created it, via a `Claude-Session: <uri>` trailer in the commit body. I can trace "who decided this" back to the conversation.

### Phase 2 — STATUS.md + portfolio

**US-5 — Every project has a living status page**
As Ryan, every repo has a `STATUS.md` that's always current. It tells me: current phase, recently shipped (with commit SHAs), what's in flight, open decisions, what's blocking progress. I never have to ask Claude "where were we."

**US-6 — Portfolio view**
As Ryan, I open `github.com/rdmilly/portfolio/README.md` and see all 7+ active projects on one page: last-touched timestamp, current phase, in-flight item. Yellow highlight on anything stale (7+ days).

**US-7 — Dashboards auto-publish**
As Ryan, when a PRD or STATUS.md changes on main, the dashboard at `dash.millyweb.com/<slug>/` regenerates automatically. No manual rebuild step.

### Phase 3 — Repos-as-nodes (locked from tonight's brainstorm)

**US-8 — Deploy-reality reconciliation** (idea #1)
As Ryan, I see on the Watchtower map when a node is running a stale commit compared to its repo's main. e.g., "VPS1 helix-cortex running @6028c6b, main at 7f2a1e9, 9 commits behind including security patch."

**US-9 — Incident bundles with code attribution** (idea #3)
As Ryan, when Watchtower fires an incident bundle (Postiz OOMs at 3am), it auto-includes the last 5 commits touching that service, with authors and diffs. Post-mortem draft writes itself.

**US-10 — "What did Claude do tonight" ledger** (idea #6)
As Ryan, every morning I see a one-screen feed: "47 commits across 6 repos overnight, 35 by Claude sessions with these PR links, 3 rolled back by CI." Ambient awareness with zero effort.

---

## 5. Functional Requirements

### 5.1 Phase 1 — Commit Pipeline (MVP)

| # | Requirement | Priority |
|---|---|---|
| G-1 | MCP tool `git_commit` registered in Provisioner, callable from any Claude session | P0 |
| G-2 | Accepts: `repo` (name or path), `intent` (free-text), `type` (feat/fix/refactor/docs/chore/test), optional `merge_target` (default main) | P0 |
| G-3 | Clones repo into cache dir on first use; pulls on subsequent calls | P0 |
| G-4 | Stages all uncommitted changes in the repo | P0 |
| G-5 | Runs `git diff --cached` and passes to Haiku with the intent to generate Conventional Commit message | P0 |
| G-6 | Creates branch named `<type>/<kebab-case-intent>` off up-to-date main | P0 |
| G-7 | Commits with generated message + `Claude-Session: <uri>` trailer | P0 |
| G-8 | Pushes branch to origin | P0 |
| G-9 | Opens PR via GitHub API, title = commit subject, body filled from template | P0 |
| G-10 | Returns PR URL to the Claude session for visibility | P0 |
| G-11 | If CI is configured on the repo, it runs automatically and blocks merge on failure | P0 |
| G-12 | On error (auth, merge conflict, dirty working tree), returns structured error with remediation steps | P0 |
| G-13 | Shows generated commit message BEFORE push for Claude's visibility/sanity check in the tool response | P1 |
| G-14 | Supports `--dry-run` returning the generated message without pushing | P1 |
| G-15 | Optional `pre_commit_hooks` execution if repo has a `.pre-commit-config.yaml` | P2 |

### 5.2 Phase 1 — Branch Protection Bootstrap

| # | Requirement | Priority |
|---|---|---|
| B-1 | `git_init_repo` MCP tool: enables branch protection on main via GitHub API | P0 |
| B-2 | Branch protection rules: block direct pushes, require CI pass, block force-push, block deletion | P0 |
| B-3 | Installs `.github/pull_request_template.md` if missing | P1 |
| B-4 | Installs basic `.github/workflows/ci.yml` running `go build/vet/test` or language-appropriate equivalent | P1 |
| B-5 | Creates `.pa.yml` repo config file (merge strategy, CI workflow name, PR template path) | P1 |
| B-6 | Applied to paving-agent first; subsequently called automatically by `git_init_repo` when repo doesn't have the file | P0 |

### 5.2b Phase 1.5 — Project Scaffolding from PRD

When a PRD is saved to KB with `type: new-project` frontmatter, the agent provisions full project infrastructure automatically. Bridges the gap between "PRD is approved" and "repo is ready for first commit."

| # | Requirement | Priority |
|---|---|---|
| SP-1 | `git_new_project` MCP tool: reads a PRD from KB, creates the repo, scaffolds it, registers dashboard | P0 |
| SP-2 | Recognizes PRD frontmatter fields: `project`, `type` (new-project / feature / architecture / process), `repo` (optional slug override) | P0 |
| SP-3 | Creates GitHub repo under `rdmilly/<slug>` — public, MIT license, language-appropriate `.gitignore` | P0 |
| SP-4 | Scaffolds: README.md (auto-generated from PRD overview), LICENSE, docs/PRD.md (copied from KB), empty STATUS.md using fixed schema | P0 |
| SP-5 | Installs `.github/pull_request_template.md`, `.github/workflows/ci.yml`, `.pa.yml` via `git_init_repo` | P0 |
| SP-6 | Creates placeholder dashboard entry at `dash.millyweb.com/<slug>/` with "Project initialized — PRD pending first render" HTML | P0 |
| SP-7 | Registers the repo in the portfolio aggregator (Phase 2 P-2 cron picks it up automatically on next run via STATUS.md presence) | P0 |
| SP-8 | Stops before writing any code — scaffolding is infrastructure, not implementation. First real commits define the code architecture. | P0 |
| SP-9 | Emits `project.scaffolded` observer event with repo URL + dashboard URL for visibility in the overnight ledger | P0 |
| SP-10 | `git_backfill_dashboards` one-shot tool: renders every existing PRD in KB with dashboard slug to `dash.millyweb.com/<slug>/` for retroactive coverage | P1 |

### 5.3 Phase 2 — STATUS.md Auto-Maintenance

| # | Requirement | Priority |
|---|---|---|
| S-1 | Every `git_commit` call also updates `STATUS.md` in the target repo | P0 |
| S-2 | STATUS.md has a fixed schema: phase, recently shipped, in flight, open decisions, blockers | P0 |
| S-3 | "Recently shipped" list capped at 10 most recent — older entries roll off | P0 |
| S-4 | Haiku generates the "shipped" entry from the commit diff + intent | P0 |
| S-5 | A `status: chore` commit type does NOT trigger STATUS.md updates (reduces noise) | P0 |
| S-6 | Manual decisions/blockers added via separate `git_status_add` tool call (not auto-extracted) | P0 |
| S-7 | STATUS.md changes bundled in the same PR as the code change | P0 |

### 5.4 Phase 2 — Portfolio Aggregator

| # | Requirement | Priority |
|---|---|---|
| P-1 | Meta-repo `github.com/rdmilly/portfolio` with `README.md` as the index | P0 |
| P-2 | Hourly cron on VPS1 pulls STATUS.md from every rdmilly repo, rebuilds index | P0 |
| P-3 | Index shows: project name, last-touched, current phase, in-flight item (one line each) | P0 |
| P-4 | Stale indicator: ⚠ if 7+ days since last commit, red if 14+ | P0 |
| P-5 | Renderable directly from GitHub (no hosting needed) | P0 |
| P-6 | Also published to `dash.millyweb.com/portfolio/` via MinIO (mobile-friendly URL) | P1 |

### 5.5 Phase 2 — Dashboard Auto-Publish

| # | Requirement | Priority |
|---|---|---|
| D-1 | GitHub Actions workflow in each repo that has a PRD | P0 |
| D-2 | Triggers: push to main touching `PRD.md`, `STATUS.md`, or `docs/**` | P0 |
| D-3 | Runs a shared renderer action (Go template or pandoc) | P0 |
| D-4 | Uploads rendered HTML to `dashboards` MinIO bucket at `<project-slug>/index.html` | P0 |
| D-5 | Served via Traefik at `dash.millyweb.com/<project-slug>/` | P0 |
| D-6 | Link to the dashboard included in STATUS.md header | P1 |

### 5.6 Phase 3 — Repos-as-Nodes

Scoped to 3 locked hero features; all others stay in Phase 4+ roadmap.

| # | Requirement | Priority |
|---|---|---|
| N-1 | **Deploy-reality reconciliation** — Watchtower diffs declared-vs-running image SHAs, alerts on drift | P1 |
| N-2 | **Incident bundles with code attribution** — incident events auto-include last 5 commits to affected service with author + diff | P1 |
| N-3 | **"What did Claude do tonight" ledger** — daily rollup of commits by Claude sessions, aggregated on portfolio dashboard | P1 |

---

## 6. Architecture

### 6.1 Service topology

```
      Claude session
            |
            v
    Provisioner MCP registry
            |
            v
    git-agent HTTP service (VPS1, port 9093)
      /         \        \
  gh CLI    git binary   Haiku API
      \         /        (OpenRouter)
       \       /
        v     v
     GitHub.com
```

### 6.2 Components

- **HTTP service:** Python FastAPI, ~500 lines. Lives at `/opt/projects/git-agent/` on VPS1. Port 9093 (internal only).
- **MCP wrapper:** registered in Provisioner, routes tool calls to the HTTP service.
- **Cache dir:** `/opt/data/git-agent/cache/` — one subdirectory per repo, shallow clone by default.
- **Auth:** reads `GITHUB_TOKEN` from Infisical at startup, caches for session, refreshes hourly.
- **Haiku:** OpenRouter anthropic/claude-haiku for commit message generation (~$0.0002/commit). Falls back to direct Anthropic API if OpenRouter down.
- **Logs:** all tool calls emit Helix `git.commit` observer events for audit.

### 6.3 State management

- Agent is stateless between calls (safe to restart)
- All durable state is in Git itself + Infisical for secrets
- Repo cache is rebuildable on demand

---

## 7. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Commit pipeline latency | < 10s p95 (clone/pull + diff + Haiku + commit + push + PR open) |
| Haiku cost per commit | < $0.001 |
| Concurrent calls | 5 simultaneous (one per active Claude session) |
| Repo cache size cap | 5 GB, LRU eviction |
| Uptime requirement | 99% (agent is soft dependency; Ryan can still git manually) |
| Service restart time | < 5 seconds |
| Credential rotation | hourly from Infisical |

---

## 8. Success Metrics

- 100% of Claude-originated commits go through the agent (measured via observer events)
- 0 direct-to-main pushes after Phase 1 ships (enforced by branch protection)
- STATUS.md freshness: no active project shows "stale 7+ days" on the portfolio dashboard
- Ryan reports he no longer asks "where were we" at session start
- PR review time drops because commit messages are diff-grounded and accurate
- 0 hallucinated "decisions" in STATUS.md after 2 weeks of dogfooding (measured by Ryan's reject-PR rate)

---

## 9. Phase Plan

### Phase 1 — Commit pipeline (target: 1 session, ~3 hours)
- Scaffold `git-agent` repo
- FastAPI service with `git_commit` and `git_init_repo` endpoints
- Haiku prompt for Conventional Commit generation
- Register MCP tool in Provisioner
- Apply `git_init_repo` to paving-agent as first dogfood target
- First dogfood commit: use the agent to commit the agent itself to its own repo

### Phase 2 — STATUS.md + portfolio + dashboards (target: 1–2 sessions)
- STATUS.md schema + updater inside `git_commit`
- `git_status_add` tool for manual decisions
- Portfolio meta-repo + hourly cron rebuilder
- Dashboard auto-publish GitHub Action
- Dogfood: apply to paving-agent, clair-command-center, helix

### Phase 3 — Repos-as-nodes (target: 2–3 sessions)
- N-1 Deploy-reality reconciliation
- N-2 Incident bundles with code attribution
- N-3 "What did Claude do tonight" ledger

### Phase 4+ — Roadmap (not scoped)
- PRs auto-attach runtime evidence (idea #2)
- Cross-repo impact graph (idea #4)
- Repo health as topology metric (idea #5)
- Cross-repo dependency watchtower (idea #7)
- Secret-leak tripwires (idea #8)
- Intent-model seeded from repo (idea #9)
- "Chat with this repo" MCP server (idea #10)

---

## 10. Open Questions (require Ryan's decision before Phase 1 build)

1. **Merge strategy:** squash (leaning) or merge-commit?
2. **Branch protection timing:** flip on immediately after Phase 1 ships, or dogfood for a day first?
3. **Auto-merge on green CI:** yes by default for Claude-opened PRs, or always require manual merge?
4. **STATUS.md location:** in-repo authoritative + portfolio aggregator (leaning), or portfolio-only central?
5. **Session provenance:** commit trailer (leaning) or a separate sidecar log?
6. **Phase 3 hero features:** lock ideas #1/#3/#6 as proposed, or swap one?

---

## 11. Risks

- **Haiku extraction quality:** bad commit messages mean untrustworthy git log. Mitigation: show generated message in tool response before push; caller can reject + re-run.
- **Agent is single-point-of-failure for Claude git writes:** if agent is down, Claude can't commit. Mitigation: agent has health check; Ryan can still git manually; agent is stateless so restart is trivial.
- **Branch protection locks out Ryan's own workflow:** if Ryan sometimes wants to hotfix directly, blocking him is friction. Mitigation: Ryan has admin override on his own repos; protection is aimed at Claude.
- **Commit noise if every tiny edit becomes a PR:** the "every change is a PR" rule is heavy. Mitigation: `type=chore` for trivial changes can batch or auto-merge without review.
- **Credential exposure via agent logs:** tokens could leak into error messages. Mitigation: strict redaction layer on all error output.

---

## 12. Dependencies

- `GITHUB_TOKEN` in Infisical with repo + workflow + admin:repo_hook scopes
- `OPENROUTER_API_KEY` in Infisical (already present per helix.env)
- `gh` CLI installed on VPS1 (trivial apt)
- Helix unified write pipeline (live as of tonight's bugfix)
- Provisioner MCP registration path (active)

---

## 13. Decision log

- **2026-04-19 v0.1:** initial draft written after tonight's discussion about enforcement layers. Decided on commit-agent-as-MCP-tool over discipline-stack approach because enforcement should be structural (tool surface), not cultural (skill doc).

- **2026-04-19 v0.2 — Open Questions resolved (Ryan):**
  - **Q1 Merge strategy: SQUASH-MERGE.** All PR commits collapse into one clean commit on main. Linear history.
  - **Q2 Branch protection timing: DOGFOOD FIRST.** 1–2 days with protection OFF after Phase 1 ships. Agent must prove reliable before we lock ourselves out of a direct-push escape hatch. Protection flips on after Ryan confirms stability.
  - **Q3 Auto-merge on green CI: YES** for Claude-opened PRs. Speed matters at 2am; CI is the gate, not human attention.
  - **Q4 STATUS.md location: IN-REPO AUTHORITATIVE + PORTFOLIO AGGREGATOR.** Each repo owns its STATUS.md; `github.com/rdmilly/portfolio` pulls them hourly into a single index.
  - **Q5 Session attribution: COMMIT TRAILER.** `Claude-Session: <uri>` line in commit body. Visible in `git log`, parseable by tooling, traces any SHA back to a conversation.
  - **Q6 Phase 3 scope: ALL 10 REPOS-AS-NODES IDEAS over time.** Phase 3a = hero MVP (ideas #1 deploy-reality reconciliation, #3 incident bundles with code attribution, #6 "what did Claude do tonight" ledger). Phase 3b onward = remaining 7 ideas in priority order. Scoped individually as each lands.
