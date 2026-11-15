# Runtime image for the MCP server. Kept deliberately thin: the server needs a
# Python runtime and nothing else, since it talks to qBittorrent over HTTP.
#
# Configuration is read when a tool is called, not at startup, so the image can
# answer `tools/list` without any credentials present. That is what lets
# directory services such as Glama inspect the server without access to a real
# qBittorrent instance.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Mount a config at /app/.instances.json, or point QB_MCP_CONFIG elsewhere.
ENTRYPOINT ["qb-mcp"]
