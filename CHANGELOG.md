# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-05

### Fixed

- `qb_set_limit` no longer reports a false failure when alternative speed limits
  are switched on. It now reads the mode first, explains that the effective
  limit is the alternative one, and only errors when the write really failed.
- `qb_diagnose` shows the three rate limits separately (effective global,
  alternative, current throughput) instead of presenting one number with two
  meanings. The `preferences` limit fields are bytes/second in the source, not
  KiB/second as the wiki claims.
- 400 responses that mean "missing required parameter" now say so in plain
  language and point at the compatibility document, instead of a bare status code.

### Added

- `qb_set_limit(mode="alternative")` edits the alternative limits through
  `app/setPreferences` and verifies the readback.
- `qb_set_share(mode=...)` exposes `shareLimitsMode` (default / any / all),
  which qBittorrent 5.3+ honours and older versions ignore.
- Versions below 4.6.0 (outside the audited range) are named in the tool output
  instead of being silently assumed to work.
- `mcp` dependency is now bounded (`>=1.28,<3`), CI runs the offline suite
  against both SDK majors, and a weekly scheduled job re-audits the upstream
  interface table and fails when qBittorrent changes something.

### Fixed

- **`qb_control` failed on qBittorrent 5.x.** The `pause`/`resume` endpoints were
  removed in 5.0 in favour of `stop`/`start`; the tool now picks the route from
  the instance version instead of hard-coding the 4.x names.
- **Global speed limits were silently ignored.** `qb_set_limit` posted to
  `/transfer/uploadLimit`, which is a *read* endpoint: qBittorrent answered
  `200` and changed nothing. Writes now go to `setUploadLimit` /
  `setDownloadLimit`, and every write is verified by reading back
  `up_rate_limit` / `dl_rate_limit` (which is also the correct field pair - the
  old code read `up_limit` / `dl_limit` from the wrong response).
- **`qb_set_share` returned HTTP 400 on every version.** qBittorrent requires
  `inactiveSeedingTimeLimit` since 4.6, `shareLimitAction` since 5.2 and
  `shareLimitsMode` since 5.3. The tool now always sends the 5.3 superset, which
  older versions ignore.
- **`qb_add` started downloads on 5.x despite `paused=True`.** 5.x reads the
  `stopped` form field; `paused` is ignored there. Both fields are now sent, and
  the resulting torrent state is checked after adding.
- `qb_detail` no longer prints the first 60 characters of a tracker announce URL,
  which leaked the prefix of private-tracker passkeys. Only `scheme://host` is
  shown; pass `show_tracker_urls=True` to reveal the full URL.

### Changed

- **Breaking:** `qb_list` now validates the `filter` argument against a whitelist
  and rejects unknown values. qBittorrent silently returns *every* torrent for an
  unknown filter value, so the old pass-through behaviour produced wrong results
  instead of an error. `paused` remains accepted and is translated per version.
- **Breaking:** `qb_set_share`'s `seeding_time` is now in **minutes**, matching
  qBittorrent's own unit. It previously accepted seconds and passed them straight
  through, which inflated limits by a factor of 60.
- `qb_set_share` gained `inactive_seeding_time` (minutes, default: inherit the
  global setting) and `action` (`stop` or `enable_super_seeding`). Removal
  actions are deliberately not exposed here - use `qb_remove`.
- `QBError` messages now include the response body, so qBittorrent's own
  diagnostics (for example "missing required parameter: shareLimitsMode") are
  visible instead of a bare HTTP status code.

### Added

- `docs/compatibility.json` - the interface contract, extracted from 25
  qBittorrent release tags (4.6.0 through 5.3.0) rather than from the wiki, which
  is out of date in places.
- `scripts/audit_compat.py` - regenerates that table from any set of tags; it
  refuses to emit guesses and exits non-zero when a source file cannot be read.
- `tests/test_contract.py` - generates one assertion per audited tag, so a new
  qBittorrent release shows up as a failing test rather than a surprise.
- The live CI job now runs against four containers (4.6.7, 5.1.4, 5.2.4, 5.3.0),
  one per compatibility generation, and exercises the write path end to end.

## [0.1.2] - 2026-11-03

### Changed

- Releasing a tag now also creates a GitHub Release with the built wheel and
  sdist attached. Previously a tag only reached PyPI, so the repository sidebar
  kept showing "No releases published" while PyPI already carried the version.

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

[0.1.2]: https://github.com/zxw060810/qb-mcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/zxw060810/qb-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/zxw060810/qb-mcp/releases/tag/v0.1.0
