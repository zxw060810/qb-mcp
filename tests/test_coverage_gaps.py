"""The last branches standing between us and 100% on the package.

Each case here exists for one specific uncovered span; the docstring says
which. Most are the ``except`` arms of a write tool, which only run when the
client is already broken.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

from qb_mcp import server
from qb_mcp.client import QBError
from qb_mcp.config import InstanceConfig


class _QB:
    def __init__(self) -> None:
        self.posts: list[tuple[str, Any]] = []

    def post(self, path: str, data: Any = None, **_kwargs: Any) -> None:
        self.posts.append((path, data))

    def transfer_info(self) -> dict[str, Any]:
        return {"up_limit": 0, "dl_limit": 0}


def _boom(_name: str) -> Any:
    raise QBError("refused")


def test_load_server_class_returns_fastmcp_on_mcp_1(monkeypatch) -> None:
    """server.py:40-41 - the mcp 1.x return arm of the compatibility layer."""

    class FakeFast:
        pass

    def fake_import(name: str) -> Any:
        if name == "mcp.server.mcpserver":
            raise ImportError("2.x not available")
        return SimpleNamespace(FastMCP=FakeFast)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    cls, is_v2 = server._load_server_class()

    assert cls is FakeFast
    assert is_v2 is False


def test_overview_counts_torrent_states(monkeypatch, instance: InstanceConfig) -> None:
    """server.py:226-227 - the state histogram inside qb_overview."""
    stub = SimpleNamespace(
        version=lambda: "5.2.4",
        server_state=lambda: {
            "connection_status": "connected",
            "alltime_dl": 0,
            "alltime_ul": 0,
            "dl_info_speed": 0,
            "up_info_speed": 0,
            "global_ratio": "0.00",
            "total_peer_connections": 0,
        },
        transfer_info=lambda: {},
        torrents=lambda **_: [
            {"state": "stalledUP"},
            {"state": "stalledUP"},
            {"state": "uploading"},
        ],
    )
    monkeypatch.setattr(server, "targets", lambda _name=None: [instance])
    monkeypatch.setattr(server, "client", lambda _name: stub)

    out = server.qb_overview("home")

    assert "做种中(无连接) 2" in out


def test_diagnose_reports_a_connection_failure(monkeypatch) -> None:
    """server.py:358-359 - qb_diagnose never reaches a verdict."""
    monkeypatch.setattr(server, "client", _boom)

    out = server.qb_diagnose("home")

    assert "DIAGNOSIS: cannot connect" in out


def test_set_limit_download_only_per_torrent(monkeypatch) -> None:
    """server.py:514-518 - the per-torrent download limit arm."""
    qb = _QB()
    monkeypatch.setattr(server, "client", lambda _name: qb)

    server.qb_set_limit("home", dl=100, hashes=["a" * 40])

    assert qb.posts[0][0] == "/api/v2/torrents/setDownloadLimit"


def test_set_share_surfaces_errors(monkeypatch) -> None:
    """server.py:568-569."""
    monkeypatch.setattr(server, "client", _boom)

    assert server.qb_set_share("home", ["a" * 40], ratio=1.0).startswith("ERROR:")


def test_add_surfaces_errors(monkeypatch) -> None:
    """server.py:618-619."""
    monkeypatch.setattr(server, "client", _boom)

    assert server.qb_add("home", "magnet:?xt=urn:btih:abc").startswith("ERROR:")


def test_category_surfaces_errors(monkeypatch) -> None:
    """server.py:672-673."""
    monkeypatch.setattr(server, "client", _boom)

    assert server.qb_category("home", ["a" * 40], category="films").startswith("ERROR:")
