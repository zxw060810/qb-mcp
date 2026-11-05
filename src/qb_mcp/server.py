"""MCP server exposing qBittorrent management tools.

Eleven tools, grouped by intent rather than by API endpoint:

Read (safe)
    qb_instances, qb_overview, qb_list, qb_detail, qb_diagnose

Write (state-changing)
    qb_control, qb_set_limit, qb_set_share, qb_add, qb_remove, qb_category
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from typing import Any

from . import __version__
from .capability import (
    HALTED_STATES,
    SHARE_LIMIT_SUPERSET,
    Capabilities,
    capabilities_for,
    normalize_filter,
)
from .client import QBClient, QBError
from .config import AppConfig, ConfigError, InstanceConfig, load_config
from .limits import normalize_rate, rate_text, same_rate


# mcp 2.0 renamed FastMCP to MCPServer and moved version/host/port handling
# around, so support both majors instead of pinning users to one of them.
# The lookup is dynamic so static type checkers do not have to see both
# branches at once (they disagree between versions).
def _load_server_class() -> tuple[Any, bool]:
    """Return (server class, is_mcp_v2).

    Raises:
        ImportError: when neither mcp major provides a server class.
    """
    try:
        module = importlib.import_module("mcp.server.mcpserver")
        return module.MCPServer, True
    except ImportError:
        pass
    try:
        module = importlib.import_module("mcp.server.fastmcp")
        return module.FastMCP, False
    except (ImportError, ModuleNotFoundError) as exc:
        print(
            "qb-mcp requires the 'mcp' package (1.x or 2.x). "
            "Install it with: pip install mcp",
            file=sys.stderr,
        )
        raise ImportError("mcp is not installed") from exc


try:
    _Server, _MCP_V2 = _load_server_class()
except ImportError:  # pragma: no cover
    raise SystemExit(1) from None



def _create_server() -> Any:
    """Build the MCP server, reporting our own version to clients.

    mcp 2.x takes ``version`` as a constructor argument. mcp 1.x has no such
    argument and falls back to the mcp package version, so pin the low-level
    server afterwards - otherwise clients see e.g. "1.28.1".
    """
    if _MCP_V2:
        return _Server("qb-mcp", version=__version__)
    server = _Server("qb-mcp")
    lowlevel = getattr(server, "_mcp_server", None)
    if lowlevel is not None:
        lowlevel.version = __version__
    return server


mcp = _create_server()

_CONFIG: AppConfig | None = None
_CLIENTS: dict[str, QBClient] = {}

GB = 1000**3  # SI gigabytes, matching qBittorrent WebUI totals
MB = 1024**2

STATE_LABELS = {
    "uploading": "上传中",
    "stalledUP": "做种中(无连接)",
    "forcedUP": "强制做种",
    "downloading": "下载中",
    "stalledDL": "下载停滞",
    "forcedDL": "强制下载",
    "metaDL": "获取元数据",
    "queuedUP": "排队上传",
    "queuedDL": "排队下载",
    "pausedUP": "已暂停(完成)",
    "pausedDL": "已暂停(未完成)",
    "stoppedUP": "已停止(完成)",
    "stoppedDL": "已停止(未完成)",
    "checkingUP": "校验中",
    "checkingDL": "校验中",
    "error": "错误",
    "missingFiles": "文件缺失",
}


# -- helpers ---------------------------------------------------------------


def config() -> AppConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def client(name: str) -> QBClient:
    if name not in _CLIENTS:
        _CLIENTS[name] = QBClient(config().get(name))
    return _CLIENTS[name]


def capabilities(qb: QBClient) -> Capabilities:
    """取实例的接口形状（按主版本判定，含 4.x/5.x 的端点与字段差异）。"""
    return capabilities_for(qb.version())


def targets(instance: str | None) -> list[InstanceConfig]:
    """Resolve an instance selector; ``None`` means every configured instance."""
    if instance:
        return [config().get(instance)]
    return [config().get(n) for n in config().names()]


def gb(value: Any) -> float:
    try:
        return float(value) / GB
    except (TypeError, ValueError):
        return 0.0


def speed(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if number <= 0:
        return "0"
    if number >= MB:
        return f"{number / MB:.1f} MB/s"
    return f"{number / 1024:.0f} KB/s"


def limit_text(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "?"
    return "不限" if number <= 0 else speed(number)


def slim(torrent: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields worth spending context on."""
    return {
        "name": (torrent.get("name") or "")[:70],
        "hash": (torrent.get("hash") or "")[:16],
        "state": torrent.get("state") or "",
        "progress": round(float(torrent.get("progress") or 0) * 100, 1),
        "ratio": round(float(torrent.get("ratio") or 0), 2),
        "upspeed": torrent.get("upspeed") or 0,
        "dlspeed": torrent.get("dlspeed") or 0,
        "uploaded": gb(torrent.get("uploaded")),
        "size": gb(torrent.get("size")),
        "seeders": torrent.get("num_complete") or 0,
        "leechers": torrent.get("num_incomplete") or 0,
    }


def find_hash(name: str, torrent_hash: str = "", title: str = "") -> str:
    """Resolve a torrent hash from an explicit hash or a fuzzy name match."""
    if torrent_hash:
        return torrent_hash
    if not title:
        raise QBError("provide either hash or name")
    matches = [
        t for t in client(name).torrents() if title.lower() in (t.get("name") or "").lower()
    ]
    if not matches:
        raise QBError(f"no torrent matching {title!r}")
    if len(matches) > 1:
        listing = "; ".join(f"{m['name'][:50]} ({m['hash'][:8]})" for m in matches[:5])
        raise QBError(f"{len(matches)} torrents match {title!r}: {listing}")
    return str(matches[0]["hash"])


def redact_tracker_url(url: str) -> str:
    """只保留 ``scheme://host``，丢掉路径与查询串。

    私有站点的 announce 链接常在路径或查询串里带 passkey，截断前 N 个字符
    并不能保护它（密钥前缀照样会进模型上下文），因此默认整段隐去。
    """
    raw = str(url or "").strip()
    if not raw:
        return "(no url)"
    match = re.match(r"^(https?)://([^/?#]+)", raw, re.IGNORECASE)
    if match is None:
        return "(hidden)"
    scheme, host = match.groups()
    return f"{scheme.lower()}://{host}"


def error_text(exc: Exception) -> str:
    return f"ERROR: {exc}"


# -- read tools ------------------------------------------------------------


@mcp.tool()
def qb_instances() -> str:
    """List every configured qBittorrent instance and whether it is reachable."""
    lines = ["configured instances:"]
    for name in config().names():
        instance = config().get(name)
        try:
            version = client(name).version()
            lines.append(
                f"  {name} ({instance.display}) {instance.url} "
                f"- OK, qBittorrent {version}"
            )
        except (QBError, ConfigError) as exc:
            lines.append(f"  {name} ({instance.display}) {instance.url} - FAILED: {exc}")
    return "\n".join(lines)


@mcp.tool()
def qb_overview(instance: str = "") -> str:
    """Global status for one instance, or all instances when omitted.

    Args:
        instance: instance key from .instances.json; empty means every instance.
    """
    blocks: list[str] = []
    for target in targets(instance or None):
        try:
            qb = client(target.name)
            info = qb.server_state() or qb.transfer_info()
            torrents = qb.torrents()
            states: dict[str, int] = {}
            for torrent in torrents:
                key = str(torrent.get("state") or "unknown")
                states[key] = states.get(key, 0) + 1
            ordered = sorted(states.items(), key=lambda item: -item[1])
            state_text = ", ".join(f"{STATE_LABELS.get(k, k)} {v}" for k, v in ordered)
            dspeed = speed(info.get("dl_info_speed"))
            uspeed = speed(info.get("up_info_speed"))
            total_dl = gb(info.get("alltime_dl"))
            total_ul = gb(info.get("alltime_ul"))
            blocks.append(
                "\n".join(
                    [
                        f"[{target.name}] {target.display} - {target.url}",
                        f"  version      : {qb.version()}",
                        f"  connection   : {info.get('connection_status')}",
                        f"  speed        : down {dspeed} / up {uspeed}",
                        f"  totals       : down {total_dl:.2f} GB / up {total_ul:.2f} GB",
                        f"  ratio        : {info.get('global_ratio')}",
                        f"  peers        : {info.get('total_peer_connections')}",
                        f"  torrents     : {len(torrents)} ({state_text})",
                    ]
                )
            )
        except (QBError, ConfigError) as exc:
            blocks.append(f"[{target.name}] {target.display} - {error_text(exc)}")
    return "\n\n".join(blocks)


@mcp.tool()
def qb_list(
    instance: str,
    filter: str = "all",
    category: str = "",
    sort: str = "",
    limit: int = 20,
) -> str:
    """List torrents on one instance.

    Args:
        instance: instance key from .instances.json.
        filter: all | downloading | seeding | completed | paused | active | stalled | errored.
        category: restrict to a category name.
        sort: field to sort by, e.g. uploaded, ratio, added_on, size.
        limit: maximum rows to return (1-50).
    """
    limit = max(1, min(int(limit), 50))
    try:
        qb = client(instance)
        # 服务端对未知筛选值不报错而是返回全部，所以这里先翻译 + 白名单校验，
        # 绝不把用户输入原样透传。
        selected_filter = normalize_filter(filter, capabilities(qb))
    except ValueError as exc:
        return f"ERROR: {exc}"
    except (QBError, ConfigError) as exc:
        return error_text(exc)

    params: dict[str, Any] = {"filter": selected_filter}
    if category:
        params["category"] = category
    if sort:
        params["sort"] = sort
        params["reverse"] = "true"
    try:
        torrents = qb.torrents(**params)
    except (QBError, ConfigError) as exc:
        return error_text(exc)

    if not torrents:
        return f"[{instance}] no torrents match filter={selected_filter!r}"

    rows = ["| 名称 | 状态 | 进度 | 分享率 | 上传 | 下载者 |", "|---|---|---|---|---|---|"]
    for torrent in torrents[:limit]:
        item = slim(torrent)
        rows.append(
            "| {name} | {state} | {progress}% | {ratio} | {uploaded:.2f} GB | {leechers} |".format(
                name=item["name"],
                state=STATE_LABELS.get(item["state"], item["state"]),
                progress=item["progress"],
                ratio=item["ratio"],
                uploaded=item["uploaded"],
                leechers=item["leechers"],
            )
        )
    header = f"[{instance}] {len(torrents)} torrents match, showing {min(limit, len(torrents))}"
    return header + "\n\n" + "\n".join(rows)


@mcp.tool()
def qb_detail(
    instance: str,
    hash: str = "",
    name: str = "",
    show_tracker_urls: bool = False,
) -> str:
    """Show one torrent in depth, including tracker status.

    Args:
        instance: instance key from .instances.json.
        hash: torrent hash (preferred).
        name: fuzzy name match, used when hash is empty.
        show_tracker_urls: reveal full announce URLs (they may contain a private
            tracker passkey - off by default so the secret never reaches the model).
    """
    try:
        torrent_hash = find_hash(instance, hash, name)
        qb = client(instance)
        props = qb.get("/api/v2/torrents/properties", hash=torrent_hash) or {}
        trackers = qb.trackers(torrent_hash)
    except (QBError, ConfigError) as exc:
        return error_text(exc)

    lines = [
        f"[{instance}] {props.get('name')}",
        f"  hash        : {torrent_hash}",
        f"  state       : {STATE_LABELS.get(str(props.get('state')), props.get('state'))}",
        f"  size        : {gb(props.get('total_size')):.2f} GB",
        f"  progress    : {float(props.get('progress') or 0) * 100:.1f}%",
        f"  ratio       : {props.get('share_ratio')}",
        f"  uploaded    : {gb(props.get('total_uploaded')):.2f} GB",
        f"  downloaded  : {gb(props.get('total_downloaded')):.2f} GB",
        f"  seeds/peers : {props.get('seeds_total')} / {props.get('peers_total')}",
        f"  save path   : {props.get('save_path')}",
        "",
        "  trackers:",
    ]
    for tracker in trackers:
        raw_url = str(tracker.get("url") or "")
        url = raw_url if show_tracker_urls else redact_tracker_url(raw_url)
        lines.append(
            f"    {tracker.get('status')}  peers={tracker.get('num_peers')}  {url}"
        )
    return "\n".join(lines)


@mcp.tool()
def qb_diagnose(instance: str, hash: str = "") -> str:
    """Explain why a qBittorrent instance is (or is not) uploading.

    Checks login, tracker health, listening port, peer counts, speed limits
    and torrent states, then states a conclusion.

    Args:
        instance: instance key from .instances.json.
        hash: optional torrent hash to inspect trackers for a single torrent.
    """
    try:
        qb = client(instance)
        version = qb.version()
        info = qb.server_state() or qb.transfer_info()
        prefs = qb.preferences()
        torrents = qb.torrents()
    except (QBError, ConfigError) as exc:
        return f"[{instance}] DIAGNOSIS: cannot connect - {exc}"

    findings: list[str] = []
    verdict_bits: list[str] = []

    if info.get("connection_status") != "connected":
        verdict_bits.append(f"连接状态异常({info.get('connection_status')})")
        findings.append(f"  ! connection_status = {info.get('connection_status')}")

    # Tracker health
    if hash:
        tracker_sets = [(hash, qb.trackers(hash))]
    else:
        tracker_sets = []
        for torrent in torrents[:30]:
            if torrent.get("state") in ("uploading", "stalledUP", "forcedUP"):
                tracker_sets.append((str(torrent["hash"]), qb.trackers(str(torrent["hash"]))))
                if len(tracker_sets) >= 5:
                    break

    working = failed = 0
    for _torrent_hash, trackers in tracker_sets:
        for tracker in trackers:
            status = tracker.get("status")
            if status == 2:
                working += 1
            elif status in (3, 4):
                failed += 1
    if tracker_sets:
        findings.append(f"  tracker: {working} working / {failed} failed")
        if working == 0:
            verdict_bits.append("tracker 全部不可用")

    # Listening port
    port = prefs.get("listen_port")
    findings.append(f"  监听端口: {port}")

    # Speed limits - honour the alternative-limits mode
    up_speed = info.get("up_info_speed")
    alt_mode = int(prefs.get("speed_limits_mode") or 0) == 1 or bool(
        prefs.get("use_alt_speed_limits")
    )
    effective_up = prefs.get("alt_up_limit") if alt_mode else prefs.get("up_limit")
    findings.append(
        f"  上传限速: {limit_text(effective_up)}"
        f"{' (备用限速模式已开启)' if alt_mode else ''} / 当前上传: {speed(up_speed)}"
    )
    up_limit_bytes = 0
    if isinstance(effective_up, (int, float)):
        up_limit_bytes = int(effective_up)
    elif isinstance(effective_up, str) and effective_up.isdigit():
        up_limit_bytes = int(effective_up)
    try:
        capped = up_limit_bytes > 0 and float(up_speed or 0) >= up_limit_bytes * 0.8
    except (TypeError, ValueError):
        capped = False

    # Peer supply
    leechers = sum(int(t.get("num_incomplete") or 0) for t in torrents)
    peers = int(info.get("total_peer_connections") or 0)
    findings.append(f"  连接数: {peers} / 潜在下载者: {leechers}")
    if peers == 0 and leechers == 0:
        verdict_bits.append("没有任何下载需求方")
    elif peers == 0 and leechers >= 10:
        verdict_bits.append("tracker 有需求但连不上对等方(检查端口/防火墙)")
    elif peers == 0 and leechers > 0:
        verdict_bits.append(f"需求极少(全部种子合计仅 {leechers} 个潜在下载者)")
    if capped:
        verdict_bits.append(f"上传已顶到限速上限 {limit_text(effective_up)}")

    # State distribution
    states: dict[str, int] = {}
    for torrent in torrents:
        key = str(torrent.get("state") or "unknown")
        states[key] = states.get(key, 0) + 1
    stopped = sum(v for k, v in states.items() if k.startswith(("stopped", "paused")))
    findings.append(f"  种子状态: {len(torrents)} 个, 其中停止/暂停 {stopped} 个")
    if torrents and stopped == len(torrents):
        verdict_bits.append("所有种子都处于停止/暂停状态")

    verdict = "、".join(verdict_bits) if verdict_bits else "端口、tracker、限速与需求方均正常"
    return "\n".join(
        [
            f"[{instance}] DIAGNOSIS (qBittorrent {version})",
            f"  结论: {verdict}",
            "  证据:",
            *findings,
        ]
    )


# -- write tools -----------------------------------------------------------


@mcp.tool()
def qb_control(instance: str, action: str, hashes: list[str] | None = None) -> str:
    """Change torrent state.

    Args:
        instance: instance key from .instances.json.
        action: pause | resume | reannounce | force_start.
        hashes: torrent hashes; empty means every torrent.
    """
    if action not in ("pause", "resume", "reannounce", "force_start"):
        return "ERROR: action must be one of pause, resume, reannounce, force_start"

    try:
        qb = client(instance)
        # 4.6 用 pause/resume，5.0 起改成 stop/start；路径无法用"参数超集"兼容，
        # 只能按版本二选一。
        caps = capabilities(qb)
        if action == "pause":
            endpoint = caps.pause_route
        elif action == "resume":
            endpoint = caps.resume_route
        elif action == "reannounce":
            endpoint = "/api/v2/torrents/reannounce"
        else:
            endpoint = "/api/v2/torrents/setForceStart"

        selected = hashes or [str(t["hash"]) for t in qb.torrents()]
        if not selected:
            return f"[{instance}] no torrents to act on"
        payload: dict[str, Any] = {"hashes": "|".join(selected)}
        if action == "force_start":
            payload["value"] = "true"
        qb.post(endpoint, data=payload)
    except (QBError, ConfigError) as exc:
        return error_text(exc)
    version_note = "" if caps.known else " (version unknown, assumed 4.x)"
    return f"[{instance}] {action}: applied to {len(selected)} torrent(s){version_note}"


@mcp.tool()
def qb_set_limit(
    instance: str,
    up: int | None = None,
    dl: int | None = None,
    hashes: list[str] | None = None,
) -> str:
    """Set upload/download speed limits in bytes per second (0 = unlimited).

    Args:
        instance: instance key from .instances.json.
        up: upload limit in bytes/s; 0 means unlimited.
        dl: download limit in bytes/s; 0 means unlimited.
        hashes: torrent hashes; empty means the global limit.
    """
    if up is None and dl is None:
        return "ERROR: provide up and/or dl"
    try:
        up_limit = normalize_rate(up) if up is not None else None
        dl_limit = normalize_rate(dl) if dl is not None else None
    except ValueError as exc:
        return f"ERROR: {exc}"

    try:
        qb = client(instance)
        before = qb.transfer_info()
        before_up, before_dl = before.get("up_rate_limit"), before.get("dl_rate_limit")

        if hashes:
            joined = "|".join(hashes)
            if up_limit is not None:
                qb.post(
                    "/api/v2/torrents/setUploadLimit",
                    data={"hashes": joined, "limit": str(up_limit)},
                )
            if dl_limit is not None:
                qb.post(
                    "/api/v2/torrents/setDownloadLimit",
                    data={"hashes": joined, "limit": str(dl_limit)},
                )
            scope = f"{len(hashes)} torrent(s)"
        else:
            # 注意端点名：写入是 setUploadLimit，uploadLimit 是读取端点。
            # 对读取端点 POST 会返回 200 但什么都不做（实测于 5.2.4）。
            if up_limit is not None:
                qb.post("/api/v2/transfer/setUploadLimit", data={"limit": str(up_limit)})
            if dl_limit is not None:
                qb.post("/api/v2/transfer/setDownloadLimit", data={"limit": str(dl_limit)})
            scope = "global"

        after = qb.transfer_info()
        per_torrent = (
            {str(t.get("hash")): t for t in qb.torrents()} if hashes else {}
        )
    except (QBError, ConfigError) as exc:
        return error_text(exc)

    # 写接口对所有情况都返回 200，只能靠读回判断是否真的生效。
    # 单种限速要读 torrents/info 里每个种子的 up_limit，全局限速才读 transfer/info。
    problems: list[str] = []
    if hashes:
        for torrent_hash in hashes:
            row = per_torrent.get(torrent_hash) or {}
            if up_limit is not None and not same_rate(row.get("up_limit"), up_limit):
                problems.append(
                    f"{torrent_hash[:8]}: upload limit reads back "
                    f"{rate_text(row.get('up_limit'))}, expected {rate_text(up_limit)}"
                )
            if dl_limit is not None and not same_rate(row.get("dl_limit"), dl_limit):
                problems.append(
                    f"{torrent_hash[:8]}: download limit reads back "
                    f"{rate_text(row.get('dl_limit'))}, expected {rate_text(dl_limit)}"
                )
    else:
        if up_limit is not None and not same_rate(after.get("up_rate_limit"), up_limit):
            problems.append(
                f"upload readback mismatch: wanted {rate_text(up_limit)}, "
                f"got {rate_text(after.get('up_rate_limit'))}"
            )
        if dl_limit is not None and not same_rate(after.get("dl_rate_limit"), dl_limit):
            problems.append(
                f"download readback mismatch: wanted {rate_text(dl_limit)}, "
                f"got {rate_text(after.get('dl_rate_limit'))}"
            )
    if problems:
        return f"ERROR: [{instance}] speed limit did not take effect - " + "; ".join(problems)

    return "\n".join(
        [
            f"[{instance}] speed limit updated ({scope})",
            f"  upload   : {rate_text(before_up)} -> {rate_text(after.get('up_rate_limit'))}",
            f"  download : {rate_text(before_dl)} -> {rate_text(after.get('dl_rate_limit'))}",
        ]
    )


@mcp.tool()
def qb_set_share(
    instance: str,
    hashes: list[str],
    ratio: float | None = None,
    seeding_time: int | None = None,
    inactive_seeding_time: int | None = None,
    action: str = "stop",
) -> str:
    """Set ratio and/or seeding-time limits for specific torrents.

    Args:
        instance: instance key from .instances.json.
        hashes: torrent hashes to update.
        ratio: maximum ratio; -1 means unlimited, -2 means use the global value.
        seeding_time: maximum seeding time in MINUTES (qBittorrent's unit);
            -1 unlimited, -2 use global (default).
        inactive_seeding_time: maximum inactive seeding time in MINUTES;
            -1 unlimited, -2 use global (default).
        action: what to do once a limit is reached - "stop" (default) or
            "enable_super_seeding". Removal is deliberately not offered here;
            use qb_remove for anything destructive.
    """
    if not hashes:
        return "ERROR: hashes must not be empty"
    if ratio is None and seeding_time is None and inactive_seeding_time is None:
        return "ERROR: provide ratio and/or seeding_time and/or inactive_seeding_time"

    actions = {"stop": "Stop", "enable_super_seeding": "EnableSuperSeeding"}
    if action not in actions:
        return (
            "ERROR: action must be one of stop, enable_super_seeding "
            "(use qb_remove for removal)"
        )

    # 参数按 5.3.0 的超集发送：requireParams 只校验必需项存在，多余参数被忽略，
    # 因此 4.6 / 5.0 / 5.1 / 5.2 都能吃同一条请求。
    payload: dict[str, Any] = {"hashes": "|".join(hashes)}
    defaults = {
        "ratioLimit": ratio,
        "seedingTimeLimit": seeding_time,
        "inactiveSeedingTimeLimit": inactive_seeding_time,
    }
    for field, value in defaults.items():
        payload[field] = str(value if value is not None else -2)
    payload["shareLimitAction"] = actions[action]
    payload["shareLimitsMode"] = "Default"
    assert set(SHARE_LIMIT_SUPERSET) <= set(payload), "share limit payload is incomplete"

    try:
        qb = client(instance)
        qb.post("/api/v2/torrents/setShareLimits", data=payload)
        rows = {str(t.get("hash")): t for t in qb.torrents()}
    except (QBError, ConfigError) as exc:
        return error_text(exc)

    warnings: list[str] = []
    if seeding_time is not None and seeding_time >= 0:
        for torrent_hash in hashes:
            row = rows.get(torrent_hash)
            if row is None:
                continue
            actual = row.get("seeding_time_limit")
            if actual == -2 and row.get("auto_tmm"):
                warnings.append(
                    f"{torrent_hash[:8]}: automatic torrent management is on, "
                    "so the per-torrent limit reads back as -2 (inherited)"
                )
            elif actual is not None and int(actual) != int(seeding_time):
                warnings.append(
                    f"{torrent_hash[:8]}: seeding_time_limit reads back {actual}, "
                    f"expected {seeding_time}"
                )

    lines = [
        f"[{instance}] share limit updated for {len(hashes)} torrent(s): "
        f"ratio={ratio if ratio is not None else 'unchanged'}, "
        f"seeding_time={seeding_time if seeding_time is not None else 'unchanged'} min, "
        f"inactive_seeding_time="
        f"{inactive_seeding_time if inactive_seeding_time is not None else 'unchanged'} min, "
        f"action={action}",
    ]
    lines.extend(f"  ! {item}" for item in warnings)
    return "\n".join(lines)


@mcp.tool()
def qb_add(
    instance: str,
    source: str,
    savepath: str = "",
    category: str = "",
    paused: bool = True,
) -> str:
    """Add a torrent from a URL, magnet link, or a local .torrent path.

    Args:
        instance: instance key from .instances.json.
        source: magnet link, http(s) URL, or absolute path to a .torrent file.
        savepath: target directory; empty uses the instance default.
        category: category to assign.
        paused: add in paused state (default true) so it can be reviewed first.
    """
    data: dict[str, Any] = {}
    if savepath:
        data["savepath"] = savepath
    if category:
        data["category"] = category
    try:
        qb = client(instance)
        # 4.6 读 paused，5.0 起读 stopped；两个字段发同一个值，
        # 版本识别不准时仍能停住（多余字段被忽略，实测两代都不报错）。
        flag = "true" if paused else "false"
        data["paused"] = flag
        data["stopped"] = flag
        before_hashes = {str(t.get("hash")) for t in qb.torrents()}

        if source.startswith(("magnet:", "http://", "https://")):
            data["urls"] = source
            qb.post("/api/v2/torrents/add", data=data)
        else:
            from pathlib import Path

            path = Path(source).expanduser()
            if not path.is_file():
                return f"ERROR: {source} is not a magnet, URL, or readable file"
            with path.open("rb") as handle:
                qb.post(
                    "/api/v2/torrents/add",
                    data=data,
                    files={"torrents": (path.name, handle, "application/x-bittorrent")},
                )

        added = [t for t in qb.torrents() if str(t.get("hash")) not in before_hashes]
    except (QBError, ConfigError) as exc:
        return error_text(exc)

    warning = ""
    if paused and len(added) == 1:
        state = str(added[0].get("state") or "")
        if state and state not in HALTED_STATES:
            warning = (
                f"\n  ! expected a stopped torrent but its state is {state!r}; "
                "the pause field may have been ignored by this qBittorrent version"
            )
    return f"[{instance}] torrent added (paused={paused}): {source[:60]}{warning}"


@mcp.tool()
def qb_remove(instance: str, hashes: list[str], delete_files: bool = False) -> str:
    """Remove torrents. Downloaded files are kept unless delete_files is true.

    Args:
        instance: instance key from .instances.json.
        hashes: torrent hashes to remove.
        delete_files: also delete the downloaded data (default false).
    """
    if not hashes:
        return "ERROR: hashes must not be empty"
    try:
        client(instance).post(
            "/api/v2/torrents/delete",
            data={
                "hashes": "|".join(hashes),
                "deleteFiles": "true" if delete_files else "false",
            },
        )
    except (QBError, ConfigError) as exc:
        return error_text(exc)
    suffix = " including downloaded files" if delete_files else " (files kept)"
    return f"[{instance}] removed {len(hashes)} torrent(s){suffix}"


@mcp.tool()
def qb_category(
    instance: str,
    hashes: list[str],
    category: str = "",
    tags: list[str] | None = None,
) -> str:
    """Assign a category and/or tags to torrents.

    Args:
        instance: instance key from .instances.json.
        hashes: torrent hashes to update.
        category: category name; empty leaves it unchanged.
        tags: tags to add.
    """
    if not hashes:
        return "ERROR: hashes must not be empty"
    try:
        qb = client(instance)
        joined = "|".join(hashes)
        if category:
            qb.post("/api/v2/torrents/setCategory", data={"hashes": joined, "category": category})
        if tags:
            qb.post("/api/v2/torrents/addTags", data={"hashes": joined, "tags": ",".join(tags)})
    except (QBError, ConfigError) as exc:
        return error_text(exc)
    return f"[{instance}] category/tags updated for {len(hashes)} torrent(s)"


# -- entry point -----------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(prog="qb-mcp", description="MCP server for qBittorrent")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio for local MCP clients (default), http for a network server",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address for http transport")
    parser.add_argument("--port", type=int, default=17890, help="port for http transport")
    parser.add_argument("--config", default="", help="path to .instances.json")
    parser.add_argument("--version", action="version", version=f"qb-mcp {__version__}")
    args = parser.parse_args()

    if args.config:
        import os

        os.environ["QB_MCP_CONFIG"] = args.config

    if args.transport == "stdio":
        mcp.run()
    elif _MCP_V2:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        settings = getattr(mcp, "settings", None)
        if settings is not None:
            settings.host = args.host
            settings.port = args.port
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
