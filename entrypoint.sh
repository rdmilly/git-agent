#!/bin/sh
# git-agent container entrypoint.
#
# Secrets MUST be present in the container environment at runtime.  The
# host's docker-compose.yml is responsible for loading them from a
# gitignored .env file (which in turn is written at deploy time from
# Infisical).  This script simply validates they're present, configures
# git's global identity, and starts uvicorn.
#
# We deliberately do NOT fetch secrets from Infisical here — doing so
# would require Infisical credentials inside the image.  Keep the runtime
# dumb; orchestration lives one layer up.

set -e

# --- Validate required env ---
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "FATAL: set OPENROUTER_API_KEY and/or ANTHROPIC_API_KEY" >&2
    exit 1
fi

# --- Configure git identity so commits don't fail ---
git config --global user.name "${GIT_COMMITTER_NAME:-git-agent}"
git config --global user.email "${GIT_COMMITTER_EMAIL:-git-agent@millyweb.internal}"
git config --global init.defaultBranch main

# --- Ensure cache dir exists inside the container ---
mkdir -p "${GIT_AGENT_CACHE_ROOT:-/opt/data/git-agent/cache}"

# --- Launch the FastAPI app ---
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 9093 \
    --log-level info
