"""Shared fixtures for the qb-mcp test suite.

Every test runs offline: the qBittorrent HTTP API is replaced by a fake
session so no live instance is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from qb_mcp.config import InstanceConfig  # noqa: E402


class FakeResponse:
    """Stand-in for requests.Response."""

    def __init__(self, status_code: int = 200, text: str = "", payload: Any = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.content = b"x" if (payload is not None or text) else b""

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records requests and replays a queued response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.verify = True

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.response

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


@pytest.fixture()
def instance() -> InstanceConfig:
    return InstanceConfig(
        name="test",
        url="http://127.0.0.1:8080",
        username="admin",
        password="secret",
        label="Test",
    )
