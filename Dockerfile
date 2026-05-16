# git-agent Dockerfile
#
# Runtime image for the git-agent MCP service.  Bundles `git` + `gh` CLI
# since both are called as subprocesses.  Secrets are NEVER baked in —
# they're injected via env at container start (see entrypoint.sh).
#
# Image is published to ghcr.io/rdmilly/git-agent on every push to main.

FROM python:3.12-slim

# --- System deps: git + gh CLI + curl (for gh apt-key fetch) ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        gnupg && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Verify the binaries we depend on are actually present.
RUN git --version && gh --version

# --- Python deps ---
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY patch_mcp.py /tmp/patch_mcp.py
RUN python3 /tmp/patch_mcp.py
Runtime image for the git-agent MCP service.  Bundles `git` + `gh` CLI
# since both are called as subprocesses.  Secrets are NEVER baked in —
# they're injected via env at container start (see entrypoint.sh).
#
# Image is published to ghcr.io/rdmilly/git-agent on every push to main.

FROM python:3.12-slim

# --- System deps: git + gh CLI + curl (for gh apt-key fetch) ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        gnupg && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Verify the binaries we depend on are actually present.
RUN git --version && gh --version

# --- Python deps ---
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python3 -c "
for line in lines:
        line = line.replace(
        )
"

# --- Application code ---
COPY app/ ./app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Cache directory will be bind-mounted from the host; declare it for clarity.
VOLUME ["/opt/data/git-agent/cache"]

EXPOSE 9093

# Default identity for git commits when env isn't set (overrideable).
ENV GIT_COMMITTER_NAME="git-agent" \
    GIT_COMMITTER_EMAIL="git-agent@millyweb.internal" \
    GIT_AGENT_CACHE_ROOT=/opt/data/git-agent/cache \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/entrypoint.sh"]
