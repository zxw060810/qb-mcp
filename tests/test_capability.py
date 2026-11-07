"""能力判定与单位处理的单元测试。

覆盖缺陷：D1（控制端点）、D3/D4（分享限制参数与单位）、D5（加种字段）、
D6（筛选值白名单）。这些用例在 0.1.2 上无法通过——0.1.2 根本没有版本概念。
"""

from __future__ import annotations

import pytest

from qb_mcp import limits
from qb_mcp.capability import (
    HALTED_STATES,
    SHARE_LIMIT_SUPERSET,
    capabilities_for,
    normalize_filter,
    parse_version,
)

# -- 版本解析 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v4.6.4", (4, 6, 4)),
        ("4.6.7", (4, 6, 7)),
        ("v5.0.0", (5, 0, 0)),
        ("v5.2.4", (5, 2, 4)),
        ("v5.3.0", (5, 3, 0)),
        ("v5.0.0beta1", (5, 0, 0)),
        ("V5.2.4", (5, 2, 4)),
    ],
)
def test_parse_version_accepts_real_world_strings(raw: str, expected: tuple) -> None:
    assert parse_version(raw) == expected


@pytest.mark.parametrize("raw", ["", "unknown", "weird", None, 42])
def test_parse_version_returns_none_for_garbage(raw: object) -> None:
    assert parse_version(raw) is None


# -- 四段能力表 -------------------------------------------------------------


def test_46_uses_pause_resume_and_paused_field() -> None:
    caps = capabilities_for("v4.6.4")

    assert caps.pause_route == "/api/v2/torrents/pause"
    assert caps.resume_route == "/api/v2/torrents/resume"
    assert caps.add_stop_field == "paused"
    assert caps.paused_filter == "paused"
    assert caps.known is True


@pytest.mark.parametrize("raw", ["v5.0.0", "v5.1.4", "v5.2.4", "v5.3.0"])
def test_5x_uses_stop_start_and_stopped_field(raw: str) -> None:
    caps = capabilities_for(raw)

    assert caps.pause_route == "/api/v2/torrents/stop"
    assert caps.resume_route == "/api/v2/torrents/start"
    assert caps.add_stop_field == "stopped"
    assert caps.paused_filter == "stopped"


def test_unknown_version_degrades_to_4x_and_is_marked_unknown() -> None:
    caps = capabilities_for("")

    assert caps.known is False
    assert caps.pause_route == "/api/v2/torrents/pause"


# -- 筛选值 -----------------------------------------------------------------


def test_paused_alias_follows_the_version() -> None:
    assert normalize_filter("paused", capabilities_for("v4.6.4")) == "paused"
    assert normalize_filter("paused", capabilities_for("v5.2.4")) == "stopped"


def test_filter_is_case_insensitive() -> None:
    assert normalize_filter("PAUSED", capabilities_for("v5.2.4")) == "stopped"


def test_unknown_filter_is_rejected_instead_of_forwarded() -> None:
    with pytest.raises(ValueError):
        normalize_filter("zzz", capabilities_for("v5.2.4"))


def test_empty_filter_means_all() -> None:
    assert normalize_filter("", capabilities_for("v5.2.4")) == "all"


# -- 状态族 -----------------------------------------------------------------


def test_halted_states_cover_both_generations() -> None:
    assert "pausedUP" in HALTED_STATES
    assert "stoppedUP" in HALTED_STATES
    assert "uploading" not in HALTED_STATES


# -- 分享限制参数超集 -------------------------------------------------------


def test_share_limit_superset_is_complete() -> None:
    assert SHARE_LIMIT_SUPERSET == (
        "hashes",
        "ratioLimit",
        "seedingTimeLimit",
        "inactiveSeedingTimeLimit",
        "shareLimitAction",
        "shareLimitsMode",
    )


# -- 单位处理 ---------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [
    (None, -1),
    ("", -1),
    (0, -1),
    (-1, -1),
    ("0", -1),
    (1048576, 1048576),
    ("409600", 409600),
])
def test_normalize_rate(value: object, expected: int) -> None:
    assert limits.normalize_rate(value) == expected


def test_zero_and_minus_one_are_the_same_limit() -> None:
    assert limits.same_rate(0, -1) is True
    assert limits.same_rate(524288, 524288) is True
    assert limits.same_rate(524288, 1048576) is False


def test_normalize_rate_rejects_garbage_and_same_rate_is_false() -> None:
    with pytest.raises(ValueError):
        limits.normalize_rate("abc")
    assert limits.same_rate("abc", 1) is False
    assert limits.is_unlimited("abc") is True


def test_rate_text_marks_unlimited() -> None:
    assert limits.rate_text(0) == "不限"
    assert limits.rate_text(-1) == "不限"
    assert limits.rate_text(1_048_576) == "1.0 MB/s"
    assert limits.rate_text(409_600) == "400 KB/s"


# -- 支持区间 ---------------------------------------------------------------


@pytest.mark.parametrize("raw", ["v4.6.0", "v4.6.4", "v5.0.0", "v5.3.0"])
def test_supported_versions_are_flagged(raw: str) -> None:
    assert capabilities_for(raw).supported is True


@pytest.mark.parametrize("raw", ["v4.5.5", "v4.1.0", "", "garbage"])
def test_older_or_unknown_versions_are_not_claimed_as_supported(raw: str) -> None:
    assert capabilities_for(raw).supported is False


def test_version_note_is_empty_inside_the_audited_range() -> None:
    from qb_mcp import server

    assert server.version_note(capabilities_for("v5.2.4")) == ""
    assert "below the audited range" in server.version_note(capabilities_for("v4.5.0"))
    assert "unreadable" in server.version_note(capabilities_for("?"))


@pytest.mark.parametrize(("raw", "expected"), [("v4.6.4", 4), ("v5.2.4", 5), ("", None)])
def test_major_is_derived_from_the_version(raw: str, expected: int | None) -> None:
    assert capabilities_for(raw).major == expected
