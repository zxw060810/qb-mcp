# qb-mcp

**An MCP server for qBittorrent — manage torrents with natural language.**

[![CI](https://github.com/zxw060810/qb-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/zxw060810/qb-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/qb-mcp.svg)](https://pypi.org/project/qb-mcp/)
[![Python](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fzxw060810%2Fqb-mcp%2Fmain%2Fpyproject.toml&query=%24.project.requires-python&label=python&color=blue)](https://github.com/zxw060810/qb-mcp/blob/main/pyproject.toml)
[![Coverage](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fzxw060810%2Fqb-mcp%2Fmain%2Fdocs%2Fcoverage.json&query=%24.percent&suffix=%25&label=coverage&color=brightgreen)](https://github.com/zxw060810/qb-mcp/blob/main/docs/TESTLIST.md)
[![License](https://img.shields.io/github/license/zxw060810/qb-mcp)](https://github.com/zxw060810/qb-mcp/blob/main/LICENSE)
[![MCP](https://img.shields.io/badge/MCP-streamable%20http%20%7C%20stdio-8b5cf6.svg)](https://modelcontextprotocol.io)

> This is an unofficial project. It is not affiliated with, endorsed by, or
> supported by the qBittorrent project.

**English** | [简体中文](https://github.com/zxw060810/qb-mcp/blob/main/README.zh-CN.md)

> Ask your agent "why isn't this torrent uploading?" and it can actually answer —
> tracker health, listening port, speed caps and peer supply, instead of guessing.
> Ask it to pause the seedbox and it knows qBittorrent 5.x renamed that endpoint.

`qb-mcp` wraps the qBittorrent WebUI API v2 and exposes it as eleven tools that
an AI agent can call. It manages **any number of instances**, each with its own
credentials, so a single agent can drive a home NAS and a remote seedbox at once.

## Why this exists

qBittorrent has a complete HTTP API but no MCP interface. Wrapping it means an
agent can answer "why isn't it uploading?" by actually checking tracker health,
listening ports, speed limits and peer supply — instead of guessing.

The eleven tools are grouped by **user intent**, not by API endpoint: mapping all
80+ endpoints one-to-one would produce a tool list no model can choose from.

## Features

- **Multiple instances**, each with independent credentials
- **`qb_diagnose`** — turns "why is nothing uploading?" into one call
- **Field-trimmed responses** — the API returns 30+ fields per torrent; this returns 12
- **Before/after reporting** on every write operation
- **`delete_files` is opt-in** — removing a torrent never deletes data by default
- **stdio and HTTP** transports
- Credentials never leave your machine; nothing is logged

## Quick start

```bash
pip install qb-mcp
cp instances.example.json .instances.json
# edit .instances.json with your qBittorrent URL and credentials
chmod 600 .instances.json
qb-mcp
```

## Configuration

`qb-mcp` reads `.instances.json` from the current directory, or the path in
`QB_MCP_CONFIG`.

```json
{
  "instances": {
    "nas": {
      "label": "Home NAS",
      "url": "http://192.168.1.100:8080",
      "username": "admin",
      "password": "${QB_PASS_NAS}"
    },
    "seedbox": {
      "url": "https://seedbox.example.com",
      "username": "me",
      "password": {"file": "~/.secrets/seedbox"}
    }
  }
}
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `url` | ✅ | — | Must start with `http://` or `https://` |
| `username` | ✅ | — | Per-instance; instances may use different accounts |
| `password` | ✅ | — | Literal, `"${ENV_VAR}"`, or `{"file": "/path"}` |
| `label` | — | key | Display name |
| `verify_ssl` | — | `true` | Set `false` for self-signed certificates |
| `timeout` | — | `8.0` | Per-request timeout in seconds |

**Keep `.instances.json` out of version control.** It is already listed in
`.gitignore`. `qb-mcp` prints a warning if the file is readable by group or others.

## Tools

### Read (safe)

| Tool | Purpose |
|---|---|
| `qb_instances` | List configured instances and whether each is reachable |
| `qb_overview` | Global status for one or all instances |
| `qb_list` | Torrent list with filtering, sorting and a row limit |
| `qb_detail` | One torrent in depth, including tracker status (tracker URLs are redacted unless `show_tracker_urls=True`) |
| `qb_diagnose` | Explain why an instance is or is not uploading |

### Write (state-changing)

| Tool | Purpose |
|---|---|
| `qb_control` | `pause` / `resume` / `reannounce` / `force_start` |
| `qb_set_limit` | Global or per-torrent speed limits |
| `qb_set_share` | Ratio / seeding-time / inactive-seeding-time limits (minutes) and the action taken on reaching them |
| `qb_add` | Add from magnet, URL or a local `.torrent` (paused by default) |
| `qb_remove` | Remove torrents; `delete_files` is opt-in |
| `qb_category` | Assign categories and tags |

## Client setup

**stdio** (Claude Desktop, Cursor, most local clients):

```json
{
  "mcpServers": {
    "qbittorrent": {
      "command": "qb-mcp",
      "env": { "QB_MCP_CONFIG": "/absolute/path/to/.instances.json" }
    }
  }
}
```

**HTTP** (remote or multi-client):

```bash
qb-mcp --transport http --host 127.0.0.1 --port 17890
```

```json
{
  "servers": {
    "qbittorrent": {
      "url": "http://127.0.0.1:17890/mcp",
      "transport": "streamable_http"
    }
  }
}
```

## Compatibility

| qBittorrent | WebUI API | Status |
|---|---|---|
| 4.x | 2.8+ | Supported |
| 5.x | 2.11+ | Supported (tested on 5.2.4) |

Both official builds and third-party NAS packages (QNAP QPKG, Synology SPK) work,
with the proxy quirks below already handled.

## Troubleshooting

### `login rejected (Fails.)`

qBittorrent returns **HTTP 200 even when the password is wrong** — the body is
`Fails.` instead of `Ok.`. `qb-mcp` inspects the body, so this message means the
credentials are genuinely wrong.

### Login fails behind a reverse proxy

The official API requires `Referer` or `Origin` to match the request `Host`
**exactly, including the port**. `qb-mcp` sets both from your configured `url`,
so make sure `url` uses the same host and port you would type in a browser.

### Works with curl but not through a proxy

Some reverse proxies (QNAP QTS is one) return **`204 No Content` with an empty
body** on a successful login instead of `200 Ok.`. `qb-mcp` treats any status
below 400 with an empty or `Ok.` body as success.

### `login blocked - too many failed attempts`

qBittorrent temporarily bans an IP after repeated failures. Wait a few minutes,
or check the WebUI's ban list.

## Development

```bash
git clone https://github.com/zxw060810/qb-mcp.git
cd qb-mcp
pip install -e ".[dev]"
ruff check .
mypy src
pytest
```

Tests mock the qBittorrent API; no live instance is required.

## License

[Apache-2.0](https://github.com/zxw060810/qb-mcp/blob/main/LICENSE)
