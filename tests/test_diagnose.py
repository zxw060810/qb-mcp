"""Regression tests for the verdict logic behind the read tools.

These exist because three real bugs were found against live qBittorrent
instances and, until now, were only guarded by hand-written manual checks:

* B1 - ``qb_overview`` read cumulative totals from the wrong endpoint, so
  upload/download/ratio came back empty on both machines.
* B2 - ``qb_diagnose`` called "a speed limit is configured" the same thing as
  "the speed limit is the bottleneck", even when upload was 0.
* B3 - ``qb_diagnose`` blamed "cannot reach peers" as soon as any leecher
  existed, instead of requiring a meaningful amount of demand.

Everything here is offline: the client is stubbed, no socket is opened.
"""

from __future__ import annotations

from typing import Any

import pytest

from qb_mcp import server
from qb_mcp.config import InstanceConfig


class StubQB:
    """Minimal stand-in for :class:`qb_mcp.client.QBClient`."""

    def __init__(
        self,
        *,
        version: str = "5.2.4",
        state: dict[str, Any] | None = None,
        transfer: dict[str, Any] | None = None,
        prefs: dict[str, Any] | None = None,
        torrents: list[dict[str, Any]] | None = None,
        trackers: list[dict[str, Any]] | None = None,
    ) -> None:
        self._version = version
        self._state = state if state is not None else {}
        self._transfer = transfer if transfer is not None else {}
        self._prefs = prefs if prefs is not None else {}
        self._torrents = torrents if torrents is not None else []
        self._trackers = trackers if trackers is not None else []

    def version(self) -> str:
        return self._version

    def webapi_version(self) -> str:
        return "2.15.1"

    def server_state(self) -> dict[str, Any]:
        return self._state

    def transfer_info(self) -> dict[str, Any]:
        return self._transfer

    def preferences(self) -> dict[str, Any]:
        return self._prefs

    def torrents(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return self._torrents

    def trackers(self, _torrent_hash: str) -> list[dict[str, Any]]:
        return self._trackers


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch, instance: InstanceConfig):
    """Point the server module at a stubbed client for a single instance."""

    def _install(stub: StubQB) -> StubQB:
        monkeypatch.setattr(server, "client", lambda _name: stub)
        monkeypatch.setattr(server, "targets", lambda _name=None: [instance])
        return stub

    return _install


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "connection_status": "connected",
        "dl_info_speed": 0,
        "up_info_speed": 0,
        "alltime_dl": 0,
        "alltime_ul": 0,
        "global_ratio": "0.00",
        "total_peer_connections": 0,
    }
    base.update(overrides)
    return base


# -- B1: cumulative totals must come from sync/maindata --------------------


def test_overview_reads_cumulative_totals_from_server_state(wire) -> None:
    """B1 regression: totals live in server_state, not in transfer/info."""
    wire(
        StubQB(
            state=_state(alltime_dl=200_000_000_000, alltime_ul=123_456_789_012),
            # transfer/info only carries live speeds, never the all-time sums
            transfer={"dl_info_speed": 0, "up_info_speed": 0},
        )
    )

    out = server.qb_overview("test")

    assert "down 200.00 GB" in out
    assert "up 123.46 GB" in out


def test_overview_falls_back_to_transfer_info_when_state_is_empty(wire) -> None:
    wire(StubQB(state={}, transfer={"alltime_dl": 5_000_000_000, "alltime_ul": 0}))

    out = server.qb_overview("test")

    assert "down 5.00 GB" in out


# -- B2: a configured limit is not the same as a saturated limit -----------


def test_diagnose_idle_instance_does_not_blame_the_limit(wire) -> None:
    """B2 regression: upload is 0, so the limit cannot be the bottleneck."""
    wire(
        StubQB(
            state=_state(up_info_speed=0),
            prefs={"listen_port": 634, "up_limit": 409_600, "speed_limits_mode": 0},
        )
    )

    out = server.qb_diagnose("test")

    assert "已顶到限速上限" not in out
    assert "没有任何下载需求方" in out


def test_diagnose_flags_limit_only_when_throughput_nears_the_cap(wire) -> None:
    wire(
        StubQB(
            state=_state(up_info_speed=90_000),
            prefs={"listen_port": 634, "up_limit": 100_000, "speed_limits_mode": 0},
        )
    )

    out = server.qb_diagnose("test")

    assert "已顶到限速上限" in out


def test_diagnose_below_eighty_percent_of_cap_is_not_flagged(wire) -> None:
    wire(
        StubQB(
            state=_state(up_info_speed=70_000),
            prefs={"listen_port": 634, "up_limit": 100_000, "speed_limits_mode": 0},
        )
    )

    out = server.qb_diagnose("test")

    assert "已顶到限速上限" not in out


def test_diagnose_honours_alternative_limit_mode(wire) -> None:
    wire(
        StubQB(
            state=_state(up_info_speed=45_000),
            prefs={
                "listen_port": 634,
                "up_limit": 0,
                "speed_limits_mode": 1,
                "alt_up_limit": 50_000,
            },
        )
    )

    out = server.qb_diagnose("test")

    assert "备用限速 49 KB/s（当前启用）" in out
    assert "已顶到限速上限" in out


# -- B3: peer verdicts need a meaningful amount of demand ------------------


def test_diagnose_small_leecher_count_is_reported_as_low_demand(wire) -> None:
    """B3 regression: 5 leechers is not "cannot reach peers"."""
    wire(
        StubQB(
            state=_state(total_peer_connections=0),
            torrents=[{"hash": "a" * 40, "state": "stalledUP", "num_incomplete": 5}],
        )
    )

    out = server.qb_diagnose("test")

    assert "需求极少" in out
    assert "连不上对等方" not in out


def test_diagnose_many_leechers_does_blame_peer_reachability(wire) -> None:
    wire(
        StubQB(
            state=_state(total_peer_connections=0),
            torrents=[{"hash": "a" * 40, "state": "stalledUP", "num_incomplete": 12}],
        )
    )

    out = server.qb_diagnose("test")

    assert "连不上对等方" in out


def test_diagnose_reports_when_every_torrent_is_stopped(wire) -> None:
    wire(
        StubQB(
            state=_state(total_peer_connections=1),
            torrents=[
                {"hash": "a" * 40, "state": "pausedUP", "num_incomplete": 0},
                {"hash": "b" * 40, "state": "stoppedUP", "num_incomplete": 0},
            ],
        )
    )

    out = server.qb_diagnose("test")

    assert "所有种子都处于停止/暂停状态" in out


def test_diagnosis_header_reports_the_webapi_version(wire) -> None:
    """接口版本是排查"这个版本支持什么"的第一个线索，必须显示。"""
    wire(StubQB(state=_state(), prefs={"listen_port": 634}))

    out = server.qb_diagnose("test")

    assert "接口 2.15.1" in out
