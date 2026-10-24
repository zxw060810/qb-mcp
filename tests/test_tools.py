"""Tests for formatting helpers and the field-trimming contract."""

from __future__ import annotations

from qb_mcp.server import GB, MB, gb, limit_text, slim, speed


def test_gb_uses_si_units():
    assert GB == 1000**3
    assert gb(2 * 1000**3) == pytest.approx(2.0)


def test_speed_formats_bytes_per_second():
    assert speed(0) == "0"
    assert speed(409600) == "400 KB/s"
    assert speed(2 * MB) == "2.0 MB/s"


def test_speed_handles_garbage():
    assert speed(None) == "?"
    assert speed("abc") == "?"


def test_limit_text_zero_means_unlimited():
    assert limit_text(0) == "不限"
    assert limit_text(409600) == "400 KB/s"
    assert limit_text(None) == "?"


def test_slim_keeps_only_curated_fields():
    torrent = {
        "name": "A" * 100,
        "hash": "abcdef0123456789deadbeef",
        "state": "stalledUP",
        "progress": 1.0,
        "ratio": 2.5,
        "upspeed": 100,
        "dlspeed": 0,
        "uploaded": 2 * 1000**3,
        "size": 4 * 1000**3,
        "num_complete": 10,
        "num_incomplete": 3,
        # fields that must be dropped
        "magnet_uri": "magnet:?xt=urn:btih:...",
        "piece_size": 4194304,
        "created_by": "qBittorrent",
    }
    trimmed = slim(torrent)
    assert set(trimmed) == {
        "name",
        "hash",
        "state",
        "progress",
        "ratio",
        "upspeed",
        "dlspeed",
        "uploaded",
        "size",
        "seeders",
        "leechers",
    }
    assert len(trimmed["name"]) == 70
    assert len(trimmed["hash"]) == 16
    assert trimmed["progress"] == 100.0
    assert trimmed["uploaded"] == pytest.approx(2.0)


def test_slim_tolerates_missing_fields():
    trimmed = slim({})
    assert trimmed["name"] == ""
    assert trimmed["progress"] == 0.0
    assert trimmed["ratio"] == 0.0


import pytest  # noqa: E402  (used by the assertions above)
