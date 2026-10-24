"""MCP server exposing qBittorrent management tools.

Eleven tools, grouped by intent rather than by API endpoint:

Read (safe)
    qb_instances, qb_overview, qb_list, qb_detail, qb_diagnose

Write (state-changing)
    qb_control, qb_set_limit, qb_set_share, qb_add, qb_remove, qb_category
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

try:  # pragma: no cover - import guard
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    print(
        "qb-mcp requires the 'mcp' package. Install it with: pip install mcp",
        file=sys.stderr,
    )
    raise

from . import __version__
from .client import QBClient, QBError
from .config import AppConfig, ConfigError, InstanceConfig, load_config

mcp = FastMCP("qb-mcp")
# FastMCP exposes no version argument and its low-level server falls back to
# the mcp package version, so clients would see something like "1.28.1".
# Pin it to ours so the handshake reports the real thing.
if getattr(mcp, "_mcp_server", None) is not None:
    mcp._mcp_server.version = __version__

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
    params: dict[str, Any] = {"filter": filter or "all"}
    if category:
        params["category"] = category
    if sort:
        params["sort"] = sort
        params["reverse"] = "true"
    try:
        torrents = client(instance).torrents(**params)
    except (QBError, ConfigError) as exc:
        return error_text(exc)

    if not torrents:
        return f"[{instance}] no torrents match filter={filter!r}"

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
def qb_detail(instance: str, hash: str = "", name: str = "") -> str:
    """Show one torrent in depth, including tracker status.

    Args:
        instance: instance key from .instances.json.
        hash: torrent hash (preferred).
        name: fuzzy name match, used when hash is empty.
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
        url = str(tracker.get("url") or "")[:60]
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
    endpoints = {
        "pause": "/api/v2/torrents/pause",
        "resume": "/api/v2/torrents/resume",
        "reannounce": "/api/v2/torrents/reannounce",
        "force_start": "/api/v2/torrents/setForceStart",
    }
    if action not in endpoints:
        return f"ERROR: action must be one of {', '.join(endpoints)}"

    try:
        qb = client(instance)
        selected = hashes or [str(t["hash"]) for t in qb.torrents()]
        if not selected:
            return f"[{instance}] no torrents to act on"
        payload: dict[str, Any] = {"hashes": "|".join(selected)}
        if action == "force_start":
            payload["value"] = "true"
        qb.post(endpoints[action], data=payload)
    except (QBError, ConfigError) as exc:
        return error_text(exc)
    return f"[{instance}] {action}: applied to {len(selected)} torrent(s)"


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
        qb = client(instance)
        before = qb.transfer_info()
        before_up = limit_text(before.get("up_limit"))
        before_dl = limit_text(before.get("dl_limit"))

        if hashes:
            if up is not None:
                qb.post(
                    "/api/v2/torrents/setUploadLimit",
                    data={"hashes": "|".join(hashes), "limit": str(up)},
                )
            if dl is not None:
                qb.post(
                    "/api/v2/torrents/setDownloadLimit",
                    data={"hashes": "|".join(hashes), "limit": str(dl)},
                )
            scope = f"{len(hashes)} torrent(s)"
        else:
            if up is not None:
                qb.post("/api/v2/transfer/uploadLimit", data={"limit": str(up)})
            if dl is not None:
                qb.post("/api/v2/transfer/downloadLimit", data={"limit": str(dl)})
            scope = "global"

        after = qb.transfer_info()
    except (QBError, ConfigError) as exc:
        return error_text(exc)

    return "\n".join(
        [
            f"[{instance}] speed limit updated ({scope})",
            f"  upload   : {before_up} -> {limit_text(after.get('up_limit'))}",
            f"  download : {before_dl} -> {limit_text(after.get('dl_limit'))}",
        ]
    )


@mcp.tool()
def qb_set_share(
    instance: str,
    hashes: list[str],
    ratio: float | None = None,
    seeding_time: int | None = None,
) -> str:
    """Set ratio and/or seeding-time limits for specific torrents.

    Args:
        instance: instance key from .instances.json.
        hashes: torrent hashes to update.
        ratio: maximum ratio; -1 means unlimited.
        seeding_time: maximum seeding time in seconds; -1 means unlimited.
    """
    if not hashes:
        return "ERROR: hashes must not be empty"
    if ratio is None and seeding_time is None:
        return "ERROR: provide ratio and/or seeding_time"
    payload: dict[str, Any] = {"hashes": "|".join(hashes)}
    if ratio is not None:
        payload["ratioLimit"] = str(ratio)
        payload["seedingTimeLimit"] = str(seeding_time if seeding_time is not None else -2)
    if seeding_time is not None:
        payload["seedingTimeLimit"] = str(seeding_time)
        payload.setdefault("ratioLimit", "-2")
    try:
        client(instance).post("/api/v2/torrents/setShareLimits", data=payload)
    except (QBError, ConfigError) as exc:
        return error_text(exc)
    return (
        f"[{instance}] share limit updated for {len(hashes)} torrent(s): "
        f"ratio={ratio if ratio is not None else 'unchanged'}, "
        f"seeding_time={seeding_time if seeding_time is not None else 'unchanged'}"
    )


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
    data: dict[str, Any] = {"paused": "true" if paused else "false"}
    if savepath:
        data["savepath"] = savepath
    if category:
        data["category"] = category
    try:
        if source.startswith("magnet:"):
            data["urls"] = source
            client(instance).post("/api/v2/torrents/add", data=data)
        elif source.startswith(("http://", "https://")):
            data["urls"] = source
            client(instance).post("/api/v2/torrents/add", data=data)
        else:
            from pathlib import Path

            path = Path(source).expanduser()
            if not path.is_file():
                return f"ERROR: {source} is not a magnet, URL, or readable file"
            with path.open("rb") as handle:
                client(instance).post(
                    "/api/v2/torrents/add",
                    data=data,
                    files={"torrents": (path.name, handle, "application/x-bittorrent")},
                )
    except (QBError, ConfigError) as exc:
        return error_text(exc)
    return f"[{instance}] torrent added (paused={paused}): {source[:60]}"


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
    else:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
