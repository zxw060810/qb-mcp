"""契约测试：实现必须与 ``docs/compatibility.json`` 一致。

这张表由 `scripts/audit_compat.py` 从 qBittorrent 源码逐 tag 抽取，是本项目
的接口契约来源。本文件不手写断言，而是逐 tag 从表里生成——qBittorrent 发新
版本时，只要重跑审计脚本，多出来的一行就会立刻变成一条失败的用例。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qb_mcp.capability import (
    FILTERS,
    HALTED_STATES,
    SHARE_LIMIT_SUPERSET,
    capabilities_for,
)

TABLE_PATH = Path(__file__).resolve().parents[1] / "docs" / "compatibility.json"
TABLE = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
ROWS = TABLE["tags"]
IDS = [row["tag"].replace("release-", "") for row in ROWS]


def test_table_covers_every_supported_generation() -> None:
    tags = {row["tag"] for row in ROWS}

    assert "release-4.6.0" in tags
    assert "release-5.0.0" in tags
    assert "release-5.2.0" in tags
    assert "release-5.3.0" in tags
    assert len(tags) >= 20, "审计表疑似被截断"


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_control_routes_match_the_audited_table(row: dict) -> None:
    caps = capabilities_for(row["tag"].replace("release-", ""))
    audited = set(row["control_endpoints"])

    if "pause" in audited:
        assert caps.pause_route.endswith("/torrents/pause")
        assert caps.resume_route.endswith("/torrents/resume")
    else:
        assert caps.pause_route.endswith("/torrents/stop")
        assert caps.resume_route.endswith("/torrents/start")


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_add_pause_field_matches_the_audited_table(row: dict) -> None:
    caps = capabilities_for(row["tag"].replace("release-", ""))

    assert caps.add_stop_field == row["add_pause_field"]


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_share_limit_payload_is_a_superset_of_what_the_version_requires(row: dict) -> None:
    required = set(row["share_limit_required"] or [])

    assert required, f"{row['tag']} 的必需参数缺失，审计表可能不完整"
    assert required <= set(SHARE_LIMIT_SUPERSET), (
        f"{row['tag']} 需要 {sorted(required - set(SHARE_LIMIT_SUPERSET))}，"
        "超集常量必须扩充"
    )


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_state_labels_cover_every_state_string_of_that_version(row: dict) -> None:
    states = set(row["state_strings"] or [])

    assert states, f"{row['tag']} 的状态字符串为空"
    assert states <= HALTED_STATES | {"uploading", "downloading"}, (
        f"{row['tag']} 出现未登记的状态字符串：{sorted(states - HALTED_STATES)}"
    )


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_filter_vocabulary_is_known(row: dict) -> None:
    vocab = set(row["filter_vocab"] or [])

    assert vocab, f"{row['tag']} 的筛选词为空"
    assert vocab <= FILTERS, f"{row['tag']} 出现未知筛选词：{sorted(vocab - FILTERS)}"


def test_table_declares_its_integrity_rule() -> None:
    """表本身必须声明"没取到就写 unavailable"，否则残缺数据会被当成事实。"""
    assert "unavailable" in json.dumps(TABLE, ensure_ascii=False)
