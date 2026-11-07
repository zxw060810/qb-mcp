"""限速与分享限制的单位处理。

三条来自源码审计的事实，决定了本模块存在的必要性：

1. qBittorrent 的写接口对**所有情况**都返回 200（官方文档原文即 "200 / All
   scenarios"），所以"写到没生效"只能靠读回比对发现。
2. 但读回时 `0` 与 `-1` 都表示"不限"（全局接口甚至会把 0 改写成 -1），
   直接做相等比较必然误报，因此先归一化。
3. `setShareLimits` 的两个时长字段单位是**分钟**，源码里按 `× 60` 换算成秒
   比较，按秒透传会把时长放大 60 倍。
"""

from __future__ import annotations

from typing import Any

UNLIMITED = -1


def normalize_rate(value: Any) -> int:
    """把速率限制归一化成字节/秒或 -1（不限）。

    接受 ``None``、``""``、``0``、``-1`` 及其字符串形式，一律视为不限。
    无法解析时抛 ``ValueError``，由调用方转成用户可见的错误。
    """
    if value is None or value == "":
        return UNLIMITED
    try:
        number = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    return UNLIMITED if number <= 0 else number


def is_unlimited(value: Any) -> bool:
    """判断一个值是否表示"不限"。解析不了时按不限处理，避免误报失败。"""
    try:
        return normalize_rate(value) == UNLIMITED
    except ValueError:
        return True


def same_rate(left: Any, right: Any) -> bool:
    """比较两个速率限制是否等价（0 与 -1 视为相同）。"""
    try:
        return normalize_rate(left) == normalize_rate(right)
    except ValueError:
        return False


def expected_readback(written: Any) -> int:
    """qBittorrent 把全局限速按 KiB 整数保存，写入值会被向下取整。

    源码（SessionImpl::setGlobalUploadSpeedLimit）写的是 ``limit / 1024``，读取时
    再乘回 1024；小于等于 1024 字节的一律存成 1 KiB；0 与负数表示不限。
    因此"写什么读回什么"不成立，校验必须先算出服务端会保存成多少。
    """
    value = normalize_rate(written)
    if value == UNLIMITED:
        return UNLIMITED
    if value <= 1024:
        return 1024
    return (value // 1024) * 1024


def rate_text(value: Any) -> str:
    """人类可读的速率文本，供工具输出使用。"""
    if is_unlimited(value):
        return "不限"
    number = normalize_rate(value)
    if number >= 1024**2:
        return f"{number / 1024**2:.1f} MB/s"
    return f"{number / 1024:.0f} KB/s"
