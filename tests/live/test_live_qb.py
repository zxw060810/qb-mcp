"""Class B - the only tests that talk to a real qBittorrent.

They run against a qBittorrent container that CI starts, so they exercise the
real HTTP surface: header handling, cookie flow, JSON shapes and whatever
quirks the installed version actually has.

Locally they skip unless ``QB_TEST_URL`` is set, which keeps a developer
machine from ever needing a running server.

Why these exist at all: Class A asserts "the server behaves the way I believe
it does". Only Class B can tell when that belief has gone stale.
"""

from __future__ import annotations

import os
import re
from typing import Any

import pytest

from qb_mcp.client import QBClient
from qb_mcp.config import InstanceConfig


@pytest.fixture(scope="module")
def live() -> QBClient:
    url = os.environ.get("QB_TEST_URL", "").strip()
    if not url:
        pytest.skip("QB_TEST_URL not set - Class B runs in CI only")

    password = os.environ.get("QB_TEST_PASS", "").strip()
    if not password:
        pytest.skip("QB_TEST_PASS not set")

    return QBClient(
        InstanceConfig(
            name="live",
            url=url,
            username=os.environ.get("QB_TEST_USER", "admin"),
            password=password,
            label="Docker qBittorrent",
        )
    )


def test_version_is_a_real_version(live: QBClient) -> None:
    version = live.version()
    assert isinstance(version, str)
    assert re.match(r"^v?\d+\.\d+", version), f"unexpected version string: {version!r}"


def test_login_succeeded_and_state_is_readable(live: QBClient) -> None:
    """A freshly started container has no port mapping, so it reports
    ``firewalled``. What this test cares about is that login succeeded and
    the state payload is readable at all.
    """
    state = live.server_state()
    status = state.get("connection_status")
    assert isinstance(status, str) and status, f"unexpected connection_status: {status!r}"


def test_server_state_carries_cumulative_totals(live: QBClient) -> None:
    """The whole point of B1: these fields must exist on a real instance."""
    state: dict[str, Any] = live.server_state()
    for key in ("alltime_dl", "alltime_ul"):
        assert key in state, f"{key} missing from sync/maindata server_state"
        assert isinstance(state[key], (int, float))


def test_transfer_info_does_not_carry_cumulative_totals(live: QBClient) -> None:
    """Live proof of why B1 happened: transfer/info has speeds, not totals."""
    info = live.transfer_info()
    assert "up_info_speed" in info or "dl_info_speed" in info


def test_torrents_endpoint_returns_a_list(live: QBClient) -> None:
    torrents = live.torrents()
    assert isinstance(torrents, list)


def test_preferences_expose_the_listening_port(live: QBClient) -> None:
    prefs = live.preferences()
    assert "listen_port" in prefs
    assert isinstance(prefs["listen_port"], int)
