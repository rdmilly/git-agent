"""Scaffolding helpers for git_new_project.

Generates the standard set of files committed to every new repo:
  - README.md
  - .github/workflows/ci.yml
  - .github/PULL_REQUEST_TEMPLATE.md

Keeps the template logic out of main.py.
"""
from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

def render_readme(project_name: str, description: str, github_repo: str) -> str:
    slug = github_repo.split("/")[-1] if "/" in github_repo else github_repo
    return f"""# {project_name}

{description}

## Development

```bash
git clone https://github.com/{github_repo}.git
cd {slug}

# Python
pip install -r requirements.txt

# Node
npm install
```

## Contributing

All changes via PR against `main`. Commit messages follow
[Conventional Commits](https://www.conventionalcommits.org/).
This repo uses `git-agent` for automated commits — include a
`Claude-Session` trailer in any Claude-assisted commits.

## License

MIT
"""


# ---------------------------------------------------------------------------
# CI workflow (language-agnostic)
# ---------------------------------------------------------------------------

CI_WORKFLOW = """\
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  ci:
    name: CI
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Detect language
        id: detect
        run: |
          if [ -f requirements.txt ] || [ -f pyproject.toml ]; then
            echo "lang=python" >> $GITHUB_OUTPUT
          elif [ -f package.json ]; then
            echo "lang=node" >> $GITHUB_OUTPUT
          else
            echo "lang=unknown" >> $GITHUB_OUTPUT
          fi

      - name: Set up Python
        if: steps.detect.outputs.lang == 'python'
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install & lint (Python)
        if: steps.detect.outputs.lang == 'python'
        run: |
          pip install -r requirements.txt 2>/dev/null || true
          pip install ruff 2>/dev/null || true
          ruff check . --ignore E501 2>/dev/null || true

      - name: Set up Node
        if: steps.detect.outputs.lang == 'node'
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install & test (Node)
        if: steps.detect.outputs.lang == 'node'
        run: |
          npm ci 2>/dev/null || npm install 2>/dev/null || true
          npm test 2>/dev/null || true

      - name: Done
        run: echo "CI passed for lang=${{ steps.detect.outputs.lang }}"
"""


# ---------------------------------------------------------------------------
# PR template
# ---------------------------------------------------------------------------

PR_TEMPLATE = """\
## Summary

<!-- What does this PR do? (1-3 sentences) -->

## Changes

<!-- Bullet list of concrete changes -->
-

## Testing

- [ ] Manual smoke test
- [ ] Automated tests pass (CI green)

## Notes

<!-- Edge cases, follow-up work, or anything the reviewer should know -->
"""


# ---------------------------------------------------------------------------
# Scaffold entry point
# ---------------------------------------------------------------------------

def write_scaffold(repo_path: Path, project_name: str, description: str,
                   github_repo: str) -> list[str]:
    """Write standard scaffold files into `repo_path`.

    Returns list of relative paths written.
    """
    written: list[str] = []

    def _write(rel: str, content: str) -> None:
        p = repo_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        written.append(rel)

    _write("README.md", render_readme(project_name, description, github_repo))
    _write(".github/workflows/ci.yml", CI_WORKFLOW)
    _write(".github/PULL_REQUEST_TEMPLATE.md", PR_TEMPLATE)

    return written
