"""Server internals: compatibility layer, caches, lookup helpers and the CLI.

Kept separate from the tool tests because these exercise wiring rather than
behaviour: which class gets built, which instance gets resolved, and which
transport ``main()`` asks for.
"""

from __future__ import annotations

import runpy
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from qb_mcp import server
from qb_mcp.client import QBError
from qb_mcp.config import InstanceConfig


@pytest.fixture()
def instance() -> InstanceConfig:
    return InstanceConfig(
        name="home",
        url="http://127.0.0.1:8080",
        username="admin",
        password="x",
        label="Home",
    )


# -- compatibility layer ---------------------------------------------------


def test_create_server_passes_version_on_mcp_2(monkeypatch) -> None:
    class Fake:
        def __init__(self, name: str, version: str = "") -> None:
            self.name = name
            self.version = version

    monkeypatch.setattr(server, "_MCP_V2", True)
    monkeypatch.setattr(server, "_Server", Fake)

    built = server._create_server()

    assert built.name == "qb-mcp"
    assert built.version == server.__version__


def test_create_server_pins_lowlevel_version_on_mcp_1(monkeypatch) -> None:
    class Low:
        version = "1.28.1"

    class Fake:
        def __init__(self, name: str) -> None:
            self.name = name
            self._mcp_server = Low()

    monkeypatch.setattr(server, "_MCP_V2", False)
    monkeypatch.setattr(server, "_Server", Fake)

    built = server._create_server()

    assert built._mcp_server.version == server.__version__


def test_create_server_tolerates_a_missing_lowlevel_server(monkeypatch) -> None:
    class Fake:
        def __init__(self, name: str) -> None:
            self.name = name

    monkeypatch.setattr(server, "_MCP_V2", False)
    monkeypatch.setattr(server, "_Server", Fake)

    built = server._create_server()

    assert built.name == "qb-mcp"


def test_load_server_class_reports_a_missing_package(monkeypatch) -> None:
    import importlib

    def boom(_name: str) -> Any:
        raise ImportError("no mcp here")

    monkeypatch.setattr(importlib, "import_module", boom)

    with pytest.raises(ImportError) as excinfo:
        server._load_server_class()

    assert "mcp is not installed" in str(excinfo.value)


# -- caches ----------------------------------------------------------------


def test_config_is_loaded_once(monkeypatch, instance) -> None:
    calls = {"n": 0}

    class Cfg:
        def get(self, _name: str) -> InstanceConfig:
            return instance

        def names(self) -> list[str]:
            return ["home"]

    def fake_load() -> Cfg:
        calls["n"] += 1
        return Cfg()

    monkeypatch.setattr(server, "_CONFIG", None)
    monkeypatch.setattr(server, "load_config", fake_load)

    server.config()
    server.config()

    assert calls["n"] == 1


def test_client_is_built_once(monkeypatch, instance) -> None:
    made: list[InstanceConfig] = []

    class Cfg:
        def get(self, _name: str) -> InstanceConfig:
            return instance

    class FakeQB:
        def __init__(self, inst: InstanceConfig) -> None:
            made.append(inst)

    monkeypatch.setattr(server, "_CONFIG", Cfg())
    monkeypatch.setattr(server, "_CLIENTS", {})
    monkeypatch.setattr(server, "QBClient", FakeQB)

    first = server.client("home")
    second = server.client("home")

    assert first is second
    assert len(made) == 1


def test_targets_resolves_all_or_one(monkeypatch, instance) -> None:
    other = InstanceConfig(
        name="nas", url="http://127.0.0.1:8081", username="u", password="p", label="NAS"
    )

    class Cfg:
        def get(self, name: str) -> InstanceConfig:
            return {"home": instance, "nas": other}[name]

        def names(self) -> list[str]:
            return ["home", "nas"]

    monkeypatch.setattr(server, "_CONFIG", Cfg())

    assert [t.name for t in server.targets(None)] == ["home", "nas"]
    assert [t.name for t in server.targets("nas")] == ["nas"]


# -- lookup helpers --------------------------------------------------------


def test_find_hash_requires_a_selector(monkeypatch) -> None:
    with pytest.raises(QBError) as excinfo:
        server.find_hash("home")

    assert "provide either hash or name" in str(excinfo.value)


def test_find_hash_matches_a_title(monkeypatch, instance) -> None:
    torrents = [{"hash": "a" * 40, "name": "Alpha Movie"}, {"hash": "b" * 40, "name": "Beta"}]
    monkeypatch.setattr(server, "client", lambda _n: SimpleNamespace(torrents=lambda: torrents))

    assert server.find_hash("home", "", "alpha") == "a" * 40


def test_find_hash_reports_no_match(monkeypatch) -> None:
    monkeypatch.setattr(server, "client", lambda _n: SimpleNamespace(torrents=lambda: []))

    with pytest.raises(QBError) as excinfo:
        server.find_hash("home", "", "nothing")

    assert "no torrent matching" in str(excinfo.value)


def test_find_hash_refuses_an_ambiguous_title(monkeypatch) -> None:
    torrents = [{"hash": "a" * 40, "name": "Movie A"}, {"hash": "b" * 40, "name": "Movie B"}]
    monkeypatch.setattr(server, "client", lambda _n: SimpleNamespace(torrents=lambda: torrents))

    with pytest.raises(QBError) as excinfo:
        server.find_hash("home", "", "movie")

    assert "2 torrents match" in str(excinfo.value)


# -- error surfaces --------------------------------------------------------


def test_qb_instances_reports_each_instance(monkeypatch, instance) -> None:
    other = InstanceConfig(
        name="nas", url="http://127.0.0.1:8081", username="u", password="p", label="NAS"
    )

    class Cfg:
        def get(self, name: str) -> InstanceConfig:
            return {"home": instance, "nas": other}[name]

        def names(self) -> list[str]:
            return ["home", "nas"]

    def fake_client(name: str) -> Any:
        if name == "home":
            return SimpleNamespace(version=lambda: "5.2.4")
        raise QBError("down")

    monkeypatch.setattr(server, "_CONFIG", Cfg())
    monkeypatch.setattr(server, "client", fake_client)

    out = server.qb_instances()

    assert "OK, qBittorrent 5.2.4" in out
    assert "FAILED" in out


def test_overview_reports_a_connection_failure(monkeypatch, instance) -> None:
    monkeypatch.setattr(server, "targets", lambda _n=None: [instance])

    def boom(_name: str) -> Any:
        raise QBError("refused")

    monkeypatch.setattr(server, "client", boom)

    out = server.qb_overview("home")

    assert "ERROR: refused" in out


def test_list_reports_a_connection_failure(monkeypatch, instance) -> None:
    def boom(_name: str) -> Any:
        raise QBError("refused")

    monkeypatch.setattr(server, "client", boom)

    assert server.qb_list("home").startswith("ERROR:")


# -- diagnose branches -----------------------------------------------------


def _diag_stub(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "version": lambda: "5.2.4",
        "server_state": lambda: {
            "connection_status": "connected",
            "up_info_speed": 0,
            "total_peer_connections": 0,
        },
        "transfer_info": lambda: {},
        "preferences": lambda: {"listen_port": 634, "up_limit": 0, "speed_limits_mode": 0},
        "torrents": lambda **_: [],
        "trackers": lambda _h: [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_diagnose_flags_a_bad_connection_status(monkeypatch) -> None:
    stub = _diag_stub(
        server_state=lambda: {
            "connection_status": "firewalled",
            "up_info_speed": 0,
            "total_peer_connections": 0,
        }
    )
    monkeypatch.setattr(server, "client", lambda _n: stub)

    out = server.qb_diagnose("home")

    assert "连接状态异常(firewalled)" in out


def test_diagnose_accepts_a_torrent_hash(monkeypatch) -> None:
    seen: list[str] = []

    def trackers(torrent_hash: str) -> list[dict[str, Any]]:
        seen.append(torrent_hash)
        return [{"status": 2, "num_peers": 1, "url": "http://t"}]

    stub = _diag_stub(trackers=trackers)
    monkeypatch.setattr(server, "client", lambda _n: stub)

    out = server.qb_diagnose("home", hash="c" * 40)

    assert seen == ["c" * 40]
    assert "1 working / 0 failed" in out


def test_diagnose_samples_at_most_five_torrents(monkeypatch) -> None:
    calls = {"n": 0}

    def trackers(_h: str) -> list[dict[str, Any]]:
        calls["n"] += 1
        return []

    torrents = [{"hash": str(i) * 40, "state": "stalledUP", "num_incomplete": 0} for i in range(8)]
    stub = _diag_stub(
        torrents=lambda **_: torrents,
        trackers=trackers,
    )
    monkeypatch.setattr(server, "client", lambda _n: stub)

    server.qb_diagnose("home")

    assert calls["n"] == 5


def test_diagnose_counts_failed_trackers(monkeypatch) -> None:
    trackers = [
        {"status": 3, "num_peers": 0, "url": "http://bad1"},
        {"status": 4, "num_peers": 0, "url": "http://bad2"},
    ]
    stub = _diag_stub(
        torrents=lambda **_: [{"hash": "a" * 40, "state": "uploading", "num_incomplete": 0}],
        trackers=lambda _h: trackers,
    )
    monkeypatch.setattr(server, "client", lambda _n: stub)

    out = server.qb_diagnose("home")

    assert "0 working / 2 failed" in out
    assert "tracker 全部不可用" in out


def test_diagnose_reads_a_string_limit(monkeypatch) -> None:
    stub = _diag_stub(
        preferences=lambda: {"listen_port": 634, "up_limit": "409600", "speed_limits_mode": 0}
    )
    monkeypatch.setattr(server, "client", lambda _n: stub)

    out = server.qb_diagnose("home")

    assert "上传限速:" in out


def test_diagnose_tolerates_a_non_numeric_speed(monkeypatch) -> None:
    stub = _diag_stub(
        server_state=lambda: {
            "connection_status": "connected",
            "up_info_speed": "not-a-number",
            "total_peer_connections": 0,
        },
        preferences=lambda: {"listen_port": 634, "up_limit": 100, "speed_limits_mode": 0},
    )
    monkeypatch.setattr(server, "client", lambda _n: stub)

    out = server.qb_diagnose("home")

    assert "已顶到限速上限" not in out


# -- CLI -------------------------------------------------------------------


def test_main_defaults_to_stdio(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(server, "mcp", SimpleNamespace(run=lambda **kw: calls.append(kw)))
    monkeypatch.setattr(sys, "argv", ["qb-mcp"])

    server.main()

    assert calls == [{}]


def test_main_http_on_mcp_2(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(server, "_MCP_V2", True)
    monkeypatch.setattr(server, "mcp", SimpleNamespace(run=lambda **kw: calls.append(kw)))
    monkeypatch.setattr(
        sys, "argv", ["qb-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "9999"]
    )

    server.main()

    assert calls == [{"transport": "streamable-http", "host": "0.0.0.0", "port": 9999}]


def test_main_http_on_mcp_1_sets_settings(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    settings = SimpleNamespace(host=None, port=None)
    monkeypatch.setattr(server, "_MCP_V2", False)
    monkeypatch.setattr(
        server, "mcp", SimpleNamespace(settings=settings, run=lambda **kw: calls.append(kw))
    )
    monkeypatch.setattr(sys, "argv", ["qb-mcp", "--transport", "http", "--port", "7000"])

    server.main()

    assert settings.port == 7000
    assert calls == [{"transport": "streamable-http"}]


def test_main_http_on_mcp_1_without_settings(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(server, "_MCP_V2", False)
    monkeypatch.setattr(server, "mcp", SimpleNamespace(run=lambda **kw: calls.append(kw)))
    monkeypatch.setattr(sys, "argv", ["qb-mcp", "--transport", "http"])

    server.main()

    assert calls == [{"transport": "streamable-http"}]


def test_main_config_flag_sets_the_env_var(monkeypatch, tmp_path) -> None:
    target = tmp_path / "custom.json"
    monkeypatch.setattr(server, "mcp", SimpleNamespace(run=lambda **_kw: None))
    monkeypatch.setattr(sys, "argv", ["qb-mcp", "--config", str(target)])
    monkeypatch.delenv("QB_MCP_CONFIG", raising=False)

    server.main()

    assert sys.modules["os"].environ["QB_MCP_CONFIG"] == str(target)


def test_module_entrypoint_prints_the_version(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["qb-mcp", "--version"])

    with pytest.raises(SystemExit):
        runpy.run_module("qb_mcp.server", run_name="__main__", alter_sys=True)


def test_reported_version_matches_pyproject() -> None:
    """客户端看到的版本号必须与打包版本一致，否则升级后会被误判。"""
    import re
    from pathlib import Path

    import qb_mcp

    # 不用 tomllib：它只在 Python 3.11+ 才有，而 CI 仍然跑 3.10。
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    match = re.search(r'(?m)^version = "([^"]+)"', pyproject.read_text(encoding="utf-8"))
    assert match is not None, "pyproject.toml 里没有 version 字段"

    assert qb_mcp.__version__ == match.group(1)
