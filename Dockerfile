FROM python:3.12-slim

# System deps: git + gh CLI
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Patch FastMCP issubclass bug (Python 3.12 + MCP 1.12.4)
COPY patch_mcp.py /tmp/patch_mcp.py
RUN python3 /tmp/patch_mcp.py

COPY app/ ./app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV GIT_AGENT_CACHE_ROOT=/opt/data/git-agent/cache
EXPOSE 9093
ENTRYPOINT ["/entrypoint.sh"]
