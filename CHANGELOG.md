# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-11-03

### Fixed

- README links are now absolute, so they resolve on the PyPI project page.
  Previously the "简体中文" switcher and the LICENSE link pointed at relative
  paths that only exist in the repository.
- The Chinese README's language switcher pointed at itself instead of the
  English page.

### Changed

- Added CI, PyPI, coverage and license badges.

## [0.1.0] - 2026-11-03

First public release.

### Added

- **11 MCP tools**, grouped by intent rather than by API endpoint:
  - read: `qb_instances`, `qb_overview`, `qb_list`, `qb_detail`, `qb_diagnose`
  - write: `qb_control`, `qb_set_limit`, `qb_set_share`, `qb_add`, `qb_remove`,
    `qb_category`
- **`qb_diagnose`** explains *why* an instance is or is not uploading instead of
  dumping raw fields: it checks connection state, tracker health, listening
  port, peer supply, speed limits and torrent states, then states a verdict.
- **Arbitrary many instances**, each with its own credentials.
- **Three password forms**: plain text, `${ENV_VAR}` placeholder, or
  `{"file": "/path/to/secret"}`.
- **Two transports**: `stdio` for local MCP clients, `streamable-http` for a
  network server.
- Slimmed responses — the raw qBittorrent payloads are trimmed to the fields
  worth spending context on.
- Write operations echo the before/after value so the caller can verify the
  change.

### Fixed

Three behaviours that are not in the qBittorrent documentation and only show up
against a live instance:

- `Referer` and `Origin` must match the request `Host` **including the port**,
  otherwise the WebUI rejects the login.
- A wrong password returns **HTTP 200** with the body `Fails.`. Treating any
  200 as success turns a bad password into a confusing string of 403s later.
- A reverse proxy may return **204 with an empty body** on a successful login
  instead of `200 Ok.`.

Two more issues fixed during bring-up against real instances:

- Cumulative totals (`alltime_ul`, `alltime_dl`, `global_ratio`,
  `total_peer_connections`) live in `sync/maindata` under `server_state`.
  `transfer/info` only carries live speeds and rate limits, so reading totals
  from there returns zeros.
- `qb_diagnose` treated "a speed limit is configured" as "the speed limit is
  the bottleneck". It now requires throughput to reach 80 % of the configured
  cap, and requires at least 10 leechers before blaming peer reachability.

### Security

- `delete_files` defaults to `false`; removing a torrent never deletes data
  unless explicitly requested.
- An explicitly configured `.instances.json` path is strict — a typo raises
  instead of silently falling back to a file in the working directory.
- The config file's permissions are checked and a warning is printed when it is
  readable by group or others.

### Testing

- 124 test cases, split into two classes:
  - **offline behaviour** (118) — no socket ever opened, runs on Python
    3.10 / 3.11 / 3.12
  - **live qBittorrent** (6) — a container started by CI, the only path that can
    tell when an assumption about the real API has gone stale
- 100 % statement coverage of `qb_mcp` (543 statements).
- CI also runs an environment gate that fails the build when the interpreter is
  not the clean shape the tests assume.

[0.1.1]: https://github.com/zxw060810/qb-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/zxw060810/qb-mcp/releases/tag/v0.1.0
