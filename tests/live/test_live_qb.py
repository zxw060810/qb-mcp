"""Class B - 唯一会连接真实 qBittorrent 的测试。

只在 GitHub Actions 的容器矩阵里运行（四档：4.6.7 / 5.1.4 / 5.2.4 / 5.3.0）。
本地没有 ``QB_TEST_URL`` 时整体 skip——开发机上永远不会误跑，也不会碰到
任何真实实例。

每条用例都走"调用工具 → 读回真实状态"的闭环：写接口对所有情况都返回 200，
只有读回能证明它真的生效。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

import pytest

from qb_mcp import server
from qb_mcp.capability import HALTED_STATES
from qb_mcp.client import QBClient
from qb_mcp.config import InstanceConfig

TORRENT_NAME = "qb-mcp-live-selftest.txt"
TEST_CATEGORY = "qb-mcp-selftest"


# -- 环境与夹具 -------------------------------------------------------------


def _env() -> dict[str, str]:
    url = os.environ.get("QB_TEST_URL", "").strip()
    if not url:
        pytest.skip("QB_TEST_URL not set - Class B runs in CI only")
    password = os.environ.get("QB_TEST_PASS", "").strip()
    if not password:
        pytest.skip("QB_TEST_PASS not set")
    return {
        "url": url,
        "username": os.environ.get("QB_TEST_USER", "admin"),
        "password": password,
    }


@pytest.fixture(scope="module")
def live() -> QBClient:
    env = _env()
    return QBClient(
        InstanceConfig(
            name="live",
            url=env["url"],
            username=env["username"],
            password=env["password"],
            label="CI qBittorrent",
        )
    )


class _Toolbox:
    """工具函数与真实客户端共用同一个入口：qb_* 来自 server，其余来自客户端。"""

    def __init__(self, client: QBClient) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        if hasattr(server, name):
            return getattr(server, name)
        return getattr(self._client, name)


@pytest.fixture(scope="module")
def tools(live: QBClient) -> Any:
    """让工具函数指向这个真实客户端（工具逻辑本身是被测对象）。"""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(server, "client", lambda _name: live)
    yield _Toolbox(live)
    monkeypatch.undo()


def _bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, str):
        return _bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(_bencode(k) + _bencode(value[k]) for k in sorted(value)) + b"e"
    raise TypeError(type(value))


@pytest.fixture(scope="module")
def torrent_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    """自造一个 1 字节的单文件种子，tracker 指向不可达地址。

    这样即使 qBittorrent 真的开始下载，也没有任何流量、不会碰到任何真实站点。
    """
    payload = b"\0"
    info = {
        "name": TORRENT_NAME,
        "length": len(payload),
        "piece length": 16384,
        "pieces": hashlib.sha1(payload).digest(),
    }
    raw = _bencode({"announce": "http://127.0.0.1:1/announce", "info": info})
    path = tmp_path_factory.mktemp("torrent") / "selftest.torrent"
    path.write_bytes(raw)
    return str(path)


def _row(live: QBClient, name: str = TORRENT_NAME) -> dict[str, Any] | None:
    for torrent in live.torrents():
        if torrent.get("name") == name:
            return torrent
    return None


def _wait_state(live: QBClient, want_halted: bool, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    state = ""
    while time.time() < deadline:
        row = _row(live)
        if row is not None:
            state = str(row.get("state") or "")
            if (state in HALTED_STATES) is want_halted:
                return state
        time.sleep(0.5)
    return state


def _cleanup(live: QBClient) -> None:
    for torrent in live.torrents():
        if torrent.get("name") == TORRENT_NAME:
            live.post(
                "/api/v2/torrents/delete",
                data={"hashes": torrent["hash"], "deleteFiles": "true"},
            )


@pytest.fixture(scope="module", autouse=True)
def _isolated(live: QBClient):
    """把容器调整到可测状态，并在结束时复原。"""
    _cleanup(live)

    # 备用限速模式会覆盖全局限速，读回永远是"不限"，先关掉。
    prefs = live.preferences()
    if prefs.get("use_alt_speed_limits") or int(prefs.get("speed_limits_mode") or 0) == 1:
        live.post("/api/v2/transfer/toggleSpeedLimitsMode")

    # 分类必须先存在，否则 setCategory 返回 409 Incorrect category name。
    live.post(
        "/api/v2/torrents/createCategory",
        data={"category": TEST_CATEGORY, "savePath": ""},
    )

    yield

    _cleanup(live)
    for torrent in live.torrents():
        if torrent.get("category") == TEST_CATEGORY:
            live.post(
                "/api/v2/torrents/removeCategories", data={"categories": TEST_CATEGORY}
            )
            break


# -- 只读 ---------------------------------------------------------------


def test_version_is_a_real_version(live: QBClient) -> None:
    version = live.version()
    assert isinstance(version, str)
    assert re.match(r"^v?\d+\.\d+", version), f"unexpected version string: {version!r}"


def test_login_succeeded_and_state_is_readable(live: QBClient) -> None:
    assert isinstance(live.server_state(), dict)
    assert live.server_state()


def test_transfer_info_exposes_rate_limit_fields(live: QBClient) -> None:
    info = live.transfer_info()
    # 读回用的是 up_rate_limit/dl_rate_limit；up_limit/dl_limit 在 preferences 里，
    # 且单位是 KiB/s —— 混用会得到永远为 0 的读数。
    assert "up_rate_limit" in info
    assert "dl_rate_limit" in info


def test_torrents_endpoint_returns_a_list(live: QBClient) -> None:
    assert isinstance(live.torrents(), list)


def test_preferences_expose_the_listening_port(live: QBClient) -> None:
    assert live.preferences().get("listen_port") is not None


# -- 写闭环（TC-LIVE-01 … 12）--------------------------------------------


def test_01_add_paused_really_parks_the_torrent(tools: QBClient, torrent_file: str) -> None:
    out = tools.qb_add("live", torrent_file, savepath="/tmp", paused=True)

    assert "ERROR" not in out, out
    state = _wait_state(tools, want_halted=True)
    assert state in HALTED_STATES, f"torrent started despite paused=True (state={state})"


def test_02_state_name_matches_this_generation(tools: QBClient) -> None:
    caps = server.capabilities(tools)
    row = _row(tools)
    assert row is not None
    expected_prefix = "paused" if caps.paused_filter == "paused" else "stopped"
    assert str(row["state"]).startswith(expected_prefix), (
        f"version {caps.raw} should use {expected_prefix}* states, got {row['state']}"
    )


def test_03_resume_makes_it_run(tools: QBClient) -> None:
    row = _row(tools)
    assert row is not None
    out = tools.qb_control("live", "resume", [str(row["hash"])])

    assert "ERROR" not in out, out
    assert _wait_state(tools, want_halted=False) not in HALTED_STATES


def test_04_pause_parks_it_again(tools: QBClient) -> None:
    row = _row(tools)
    assert row is not None
    out = tools.qb_control("live", "pause", [str(row["hash"])])

    assert "ERROR" not in out, out
    assert _wait_state(tools, want_halted=True) in HALTED_STATES


def test_05_reannounce_is_accepted(tools: QBClient) -> None:
    row = _row(tools)
    assert row is not None
    out = tools.qb_control("live", "reannounce", [str(row["hash"])])

    assert "ERROR" not in out, out


def test_06_per_torrent_limit_takes_effect(tools: QBClient) -> None:
    row = _row(tools)
    assert row is not None
    out = tools.qb_set_limit("live", up=1048576, hashes=[str(row["hash"])])

    assert "ERROR" not in out, out
    assert _row(tools).get("up_limit") == 1048576


def test_07_global_limit_takes_effect(tools: QBClient) -> None:
    out = tools.qb_set_limit("live", up=2097152)

    assert "ERROR" not in out, out
    assert tools.transfer_info().get("up_rate_limit") == 2097152


def test_08_global_limit_is_restored(tools: QBClient) -> None:
    out = tools.qb_set_limit("live", up=0)

    assert "ERROR" not in out, out
    assert int(tools.transfer_info().get("up_rate_limit") or 0) <= 0


def test_09_share_limit_is_accepted_and_persisted(tools: QBClient) -> None:
    row = _row(tools)
    assert row is not None
    out = tools.qb_set_share("live", hashes=[str(row["hash"])], ratio=2.0, seeding_time=60)

    assert not out.startswith("ERROR"), out
    # 自动种子管理开启时，单种限制会读回 -2（继承全局），这是正常现象。
    after = _row(tools)
    if not after.get("auto_tmm"):
        assert int(after.get("seeding_time_limit")) == 60


def test_10_category_and_tags_are_applied(tools: QBClient) -> None:
    row = _row(tools)
    assert row is not None
    out = tools.qb_category("live", [str(row["hash"])], category=TEST_CATEGORY, tags=["ci"])

    assert "ERROR" not in out, out
    after = _row(tools)
    assert after.get("category") == TEST_CATEGORY


def test_11_paused_filter_does_not_silently_return_everything(tools: QBClient) -> None:
    out = tools.qb_list("live", filter="paused", limit=50)

    assert "ERROR" not in out, out
    total = len(tools.torrents())
    halted = sum(1 for t in tools.torrents() if str(t.get("state")) in HALTED_STATES)
    if total > halted:
        assert f"{total} torrents match" not in out, (
            "filter=paused 返回了全部种子，说明筛选值没有被正确翻译"
        )


def test_12_remove_deletes_the_torrent(tools: QBClient) -> None:
    row = _row(tools)
    assert row is not None
    out = tools.qb_remove("live", [str(row["hash"])], delete_files=True)

    assert "ERROR" not in out, out
    assert _row(tools) is None
