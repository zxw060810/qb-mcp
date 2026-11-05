"""qBittorrent 版本能力判定。

依据 `docs/compatibility.json`（对 qbittorrent/qBittorrent 25 个稳定 tag 的
源码逐条审计）。能力差异共四段：

    4.6.x    pause / resume，加种字段 paused，筛选 paused/resumed，分享限制 4 参数
    5.0-5.1  stop / start，  加种字段 stopped，筛选 stopped/running，分享限制 4 参数
    5.2.x    同 5.0，分享限制 5 参数（+shareLimitAction）
    5.3.x+   同 5.0，分享限制 6 参数（+shareLimitsMode）

设计取舍：**路径必须按版本二选一，参数则永远发超集**。qBittorrent 的
`requireParams` 只校验必需项是否存在，多余参数会被安静忽略，因此分享限制
始终发送最新版本的完整参数集合，老版本自动兼容，不必为每一段写一套拼装逻辑。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)

#: 分享限制接口自 4.6.0 起的必需参数超集（按 5.3.0 取）。
SHARE_LIMIT_SUPERSET: tuple[str, ...] = (
    "hashes",
    "ratioLimit",
    "seedingTimeLimit",
    "inactiveSeedingTimeLimit",
    "shareLimitAction",
    "shareLimitsMode",
)

#: 状态字符串的两族写法：4.6.x 用 paused*，5.0.0 起统一为 stopped*。
#: 审计覆盖的最低版本；更老的行为未经验证，调用方应提示用户。
SUPPORTED_MIN = (4, 6, 0)

PAUSED_STATES = frozenset({"pausedUP", "pausedDL"})
STOPPED_STATES = frozenset({"stoppedUP", "stoppedDL"})
HALTED_STATES = PAUSED_STATES | STOPPED_STATES


def parse_version(raw: Any) -> tuple[int, int, int] | None:
    """把 ``v5.2.4`` / ``4.6.7`` / ``v5.0.0beta1`` 解析成三元组。

    解析不出来时返回 ``None``，由调用方决定如何降级。
    """
    if not isinstance(raw, str):
        return None
    match = _VERSION_RE.match(raw.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch or 0))


@dataclass(frozen=True)
class Capabilities:
    """一个实例的接口形状。字段名全部是语义名，不出现 URL。"""

    raw: str
    version: tuple[int, int, int] | None
    pause_route: str
    resume_route: str
    add_stop_field: str
    paused_filter: str

    @property
    def known(self) -> bool:
        """版本是否解析成功。False 时调用方应当把警告写进工具输出。"""
        return self.version is not None

    @property
    def major(self) -> int | None:
        return self.version[0] if self.version else None

    @property
    def supported(self) -> bool:
        """版本是否落在审计覆盖范围内（4.6.0 及以上）。"""
        return self.version is not None and self.version >= SUPPORTED_MIN


_V4 = {
    "pause_route": "/api/v2/torrents/pause",
    "resume_route": "/api/v2/torrents/resume",
    "add_stop_field": "paused",
    "paused_filter": "paused",
}
_V5 = {
    "pause_route": "/api/v2/torrents/stop",
    "resume_route": "/api/v2/torrents/start",
    "add_stop_field": "stopped",
    "paused_filter": "stopped",
}


def capabilities_for(raw: Any) -> Capabilities:
    """按版本号给出接口形状。

    识别不出主版本时保守地按 4.x 处理（`pause`/`resume` 是 4.x 的唯一写法，
    而 5.x 上它会明确 404，不会静默产生错误结果），同时 `known` 为 False，
    调用方应提示用户升级或检查版本字符串。
    """
    version = parse_version(raw)
    table = _V5 if (version is not None and version[0] >= 5) else _V4
    return Capabilities(
        raw=str(raw or ""),
        version=version,
        pause_route=table["pause_route"],
        resume_route=table["resume_route"],
        add_stop_field=table["add_stop_field"],
        paused_filter=table["paused_filter"],
    )


#: 客户端接受的筛选值（不含 `all`）。服务端对未知筛选值不报错而是返回全部，
#: 所以这里必须白名单校验，绝不能透传。
FILTERS: frozenset[str] = frozenset(
    {
        "all",
        "downloading",
        "seeding",
        "completed",
        "paused",
        "stopped",
        "active",
        "inactive",
        "resumed",
        "running",
        "stalled",
        "stalled_uploading",
        "stalled_downloading",
        "errored",
        "checking",
        "moving",
    }
)


def normalize_filter(value: str, caps: Capabilities) -> str:
    """把用户输入的筛选值翻译成当前版本认识的词。

    - `paused` 是跨版本通用别名，按版本落到 `paused` 或 `stopped`；
    - 其余值做白名单校验；
    - 未知值抛 `ValueError`，绝不透传给服务端（否则会拿到全部种子）。
    """
    candidate = (value or "all").strip().lower()
    if candidate not in FILTERS:
        allowed = " | ".join(sorted(FILTERS))
        raise ValueError(f"unknown filter {value!r}; allowed: {allowed}")
    if candidate == "paused":
        return caps.paused_filter
    return candidate
