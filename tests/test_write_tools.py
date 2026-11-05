"""Behaviour tests for the write tools and the two read tools not yet covered.

These are the highest-risk functions in the package: they change state on a
live qBittorrent. Everything here is stubbed - no socket is opened and no
download is touched - but every outgoing request is asserted, so a wrong
endpoint or a wrong payload field fails the build.

Coverage targets: qb_list, qb_detail, qb_control, qb_set_limit, qb_set_share,
qb_add, qb_remove, qb_category.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from qb_mcp import server
from qb_mcp.client import QBError
from qb_mcp.config import InstanceConfig


class RecordQB:
    """Records every request so tests can assert on the wire payload."""

    def __init__(
        self,
        *,
        version: str = "5.2.4",
        torrents: list[dict[str, Any]] | None = None,
        transfer: dict[str, Any] | None = None,
        prefs: dict[str, Any] | None = None,
        props: dict[str, Any] | None = None,
        trackers: list[dict[str, Any]] | None = None,
    ) -> None:
        self._version = version
        self._torrents = torrents if torrents is not None else []
        self._transfer = transfer if transfer is not None else {}
        self._prefs = prefs if prefs is not None else {}
        self.apply_set_preferences = True
        self._props = props if props is not None else {}
        self._trackers = trackers if trackers is not None else []
        self.posts: list[tuple[str, Any]] = []
        self.post_kwargs: list[dict[str, Any]] = []
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.torrent_calls: list[dict[str, Any]] = []
        self._transfer_reads = 0

    def version(self) -> str:
        return self._version

    def torrents(self, **params: Any) -> list[dict[str, Any]]:
        self.torrent_calls.append(params)
        return self._torrents

    def preferences(self) -> dict[str, Any]:
        return self._prefs

    def transfer_info(self) -> dict[str, Any]:
        # 允许传一个序列，模拟"写入前 / 写入后"的两次读回。
        if isinstance(self._transfer, list):
            index = min(self._transfer_reads, len(self._transfer) - 1)
            self._transfer_reads += 1
            return self._transfer[index]
        return self._transfer

    def post(self, path: str, data: Any = None, **kwargs: Any) -> None:
        self.posts.append((path, data))
        self.post_kwargs.append(kwargs)
        # setPreferences 之后读回要看到新值，否则无法验证写后校验逻辑。
        if (
            path.endswith("/app/setPreferences")
            and isinstance(data, dict)
            and self.apply_set_preferences
        ):
            import json as _json

            self._prefs.update(_json.loads(data.get("json", "{}")))

    def get(self, path: str, **params: Any) -> Any:
        self.gets.append((path, params))
        return self._props if "properties" in path else {}

    def trackers(self, _torrent_hash: str) -> list[dict[str, Any]]:
        return self._trackers


def _torrent(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Test Torrent",
        "hash": "a" * 40,
        "state": "stalledUP",
        "progress": 0.5,
        "ratio": 1.25,
        "upspeed": 0,
        "dlspeed": 0,
        "uploaded": 1_000_000_000,
        "size": 2_000_000_000,
        "num_complete": 3,
        "num_incomplete": 1,
    }
    base.update(overrides)
    return base


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch, instance: InstanceConfig):
    def _install(stub: RecordQB) -> RecordQB:
        monkeypatch.setattr(server, "client", lambda _name: stub)
        monkeypatch.setattr(server, "targets", lambda _name=None: [instance])
        return stub

    return _install


# -- qb_list ---------------------------------------------------------------


def test_list_renders_a_table(wire) -> None:
    wire(RecordQB(torrents=[_torrent()]))

    out = server.qb_list("test")

    assert "Test Torrent" in out
    assert "50.0%" in out
    assert "1.00 GB" in out


def test_list_forwards_filters_to_the_api(wire) -> None:
    stub = wire(RecordQB(torrents=[_torrent()]))

    server.qb_list("test", filter="seeding", category="films", sort="ratio")

    assert stub.torrent_calls[0] == {
        "filter": "seeding",
        "category": "films",
        "sort": "ratio",
        "reverse": "true",
    }


def test_list_reports_when_nothing_matches(wire) -> None:
    wire(RecordQB(torrents=[]))

    out = server.qb_list("test")

    assert "no torrents match" in out


def test_list_clamps_an_oversized_limit(wire) -> None:
    wire(RecordQB(torrents=[_torrent()]))

    out = server.qb_list("test", limit=999)

    assert "showing 1" in out


# -- qb_detail -------------------------------------------------------------


def test_detail_renders_properties_and_trackers(wire) -> None:
    wire(
        RecordQB(
            props={
                "name": "Detail Me",
                "state": "uploading",
                "total_size": 3_000_000_000,
                "progress": 1.0,
                "share_ratio": 2.5,
                "total_uploaded": 4_000_000_000,
                "total_downloaded": 1_000_000_000,
                "seeds_total": 9,
                "peers_total": 4,
                "save_path": "/downloads",
            },
            trackers=[{"status": 2, "num_peers": 7, "url": "http://tracker.example/announce"}],
        )
    )

    out = server.qb_detail("test", hash="a" * 40)

    assert "Detail Me" in out
    assert "3.00 GB" in out
    assert "trackers:" in out
    assert "tracker.example" in out


def test_detail_surfaces_lookup_errors(wire) -> None:
    wire(RecordQB(torrents=[]))

    out = server.qb_detail("test", name="does-not-exist")

    assert out.startswith("ERROR:")


# -- qb_detail 脱敏（D7） ---------------------------------------------------

PRIVATE_TRACKER = "https://tracker.example/announce/SECRET_PASSKEY?credential=xyz"


def test_detail_hides_tracker_path_and_passkey_by_default(wire) -> None:
    wire(
        RecordQB(
            props={"name": "Private"},
            trackers=[{"status": 2, "num_peers": 1, "url": PRIVATE_TRACKER}],
        )
    )

    out = server.qb_detail("test", hash="a" * 40)

    assert "https://tracker.example" in out
    for secret in ("SECRET_PASSKEY", "credential", "xyz"):
        assert secret not in out


def test_detail_keeps_status_and_peer_count_after_redaction(wire) -> None:
    wire(
        RecordQB(
            props={"name": "Private"},
            trackers=[{"status": 2, "num_peers": 5, "url": PRIVATE_TRACKER}],
        )
    )

    out = server.qb_detail("test", hash="a" * 40)

    assert "peers=5" in out


def test_detail_reveals_the_full_url_only_when_asked(wire) -> None:
    wire(
        RecordQB(
            props={"name": "Private"},
            trackers=[{"status": 2, "num_peers": 1, "url": PRIVATE_TRACKER}],
        )
    )

    out = server.qb_detail("test", hash="a" * 40, show_tracker_urls=True)

    assert PRIVATE_TRACKER in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://tracker.example/announce/KEY", "https://tracker.example"),
        ("http://tracker.example:8080/a?b=c", "http://tracker.example:8080"),
        ("", "(no url)",
        ),
        ("not-a-url", "not-a-url"),
        ("** [DHT] **", "[DHT]"),
    ],
)
def test_redact_tracker_url(raw: str, expected: str) -> None:
    assert server.redact_tracker_url(raw) == expected


# -- qb_list 筛选白名单（D6） ----------------------------------------------


def test_list_translates_paused_for_5x(wire) -> None:
    stub = wire(RecordQB(torrents=[_torrent()], version="v5.2.4"))

    server.qb_list("test", filter="paused")

    assert stub.torrent_calls[0]["filter"] == "stopped"


def test_list_passes_paused_through_on_4x(wire) -> None:
    stub = wire(RecordQB(torrents=[_torrent()], version="v4.6.4"))

    server.qb_list("test", filter="paused")

    assert stub.torrent_calls[0]["filter"] == "paused"


def test_list_rejects_an_unknown_filter_without_calling_the_api(wire) -> None:
    # 服务端对未知筛选值不报错而是返回全部，透传等于静默欺骗。
    stub = wire(RecordQB(torrents=[_torrent()], version="v5.2.4"))

    out = server.qb_list("test", filter="zzz")

    assert out.startswith("ERROR:")
    assert "unknown filter" in out
    assert stub.torrent_calls == []


# -- qb_control ------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "route"),
    [
        ("v4.6.4", "/api/v2/torrents/pause"),
        ("v5.2.4", "/api/v2/torrents/stop"),
    ],
)
def test_control_pause_uses_the_version_specific_endpoint(wire, version, route) -> None:
    # D1：5.x 上 pause/resume 已被移除（实测 404），必须改走 stop/start。
    stub = wire(RecordQB(torrents=[_torrent()], version=version))

    out = server.qb_control("test", "pause")

    assert stub.posts[0][0] == route
    assert stub.posts[0][1] == {"hashes": "a" * 40}
    assert "applied to 1 torrent" in out


@pytest.mark.parametrize(
    ("version", "route"),
    [
        ("v4.6.4", "/api/v2/torrents/resume"),
        ("v5.0.0", "/api/v2/torrents/start"),
    ],
)
def test_control_resume_uses_the_version_specific_endpoint(wire, version, route) -> None:
    stub = wire(RecordQB(torrents=[_torrent()], version=version))

    server.qb_control("test", "resume")

    assert stub.posts[0][0] == route


def test_control_force_start_sends_the_value_flag(wire) -> None:
    stub = wire(RecordQB(torrents=[_torrent()]))

    server.qb_control("test", "force_start", hashes=["b" * 40])

    assert stub.posts[0] == (
        "/api/v2/torrents/setForceStart",
        {"hashes": "b" * 40, "value": "true"},
    )


def test_control_rejects_an_unknown_action(wire) -> None:
    wire(RecordQB())

    out = server.qb_control("test", "explode")

    assert out.startswith("ERROR:")
    assert "action must be one of" in out


def test_control_reports_when_there_is_nothing_to_act_on(wire) -> None:
    wire(RecordQB(torrents=[]))

    out = server.qb_control("test", "resume")

    assert "no torrents to act on" in out


# -- qb_set_limit ----------------------------------------------------------


def test_set_limit_requires_at_least_one_side(wire) -> None:
    wire(RecordQB())

    assert server.qb_set_limit("test").startswith("ERROR:")


def test_set_limit_global_posts_to_set_endpoints(wire) -> None:
    # D2：写入端点是 setUploadLimit/setDownloadLimit；uploadLimit 是读取端点，
    # 对它 POST 会返回 200 但什么都不做（实测于 5.2.4）。
    stub = wire(
        RecordQB(
            transfer=[
                {"up_rate_limit": 0, "dl_rate_limit": 0},
                {"up_rate_limit": 1024, "dl_rate_limit": 2048},
            ]
        )
    )

    out = server.qb_set_limit("test", up=1024, dl=2048)

    paths = [p for p, _ in stub.posts]
    assert paths == ["/api/v2/transfer/setUploadLimit", "/api/v2/transfer/setDownloadLimit"]
    assert stub.posts[0][1] == {"limit": "1024"}
    assert "speed limit updated" in out


def test_set_limit_reports_when_the_write_had_no_effect(wire) -> None:
    # 写接口对任何情况都返回 200，只能靠读回判断。
    wire(
        RecordQB(
            transfer=[
                {"up_rate_limit": 0, "dl_rate_limit": 0},
                {"up_rate_limit": 0, "dl_rate_limit": 0},
            ]
        )
    )

    out = server.qb_set_limit("test", up=1048576)

    assert out.startswith("ERROR:")
    assert "did not take effect" in out


def test_set_limit_treats_zero_and_minus_one_as_the_same(wire) -> None:
    stub = wire(
        RecordQB(
            transfer=[
                {"up_rate_limit": 409600, "dl_rate_limit": 0},
                {"up_rate_limit": -1, "dl_rate_limit": 0},
            ]
        )
    )

    out = server.qb_set_limit("test", up=0)

    assert not out.startswith("ERROR:")
    assert stub.posts[0][1] == {"limit": "-1"}


def test_set_limit_per_torrent_posts_to_torrent_endpoints(wire) -> None:
    stub = wire(RecordQB(transfer={"up_rate_limit": 0, "dl_rate_limit": 0}))

    server.qb_set_limit("test", up=512, hashes=["c" * 40])

    path, data = stub.posts[0]
    assert path == "/api/v2/torrents/setUploadLimit"
    assert data == {"hashes": "c" * 40, "limit": "512"}


# -- qb_set_share ----------------------------------------------------------


def test_set_share_requires_hashes(wire) -> None:
    wire(RecordQB())

    assert server.qb_set_share("test", hashes=[]).startswith("ERROR:")


def test_set_share_requires_a_value(wire) -> None:
    wire(RecordQB())

    out = server.qb_set_share("test", hashes=["a" * 40])

    assert out.startswith("ERROR:")


def test_set_share_always_sends_the_full_parameter_superset(wire) -> None:
    # D3/D4：4.6 要求 4 个参数、5.2 要求 5 个、5.3 要求 6 个；超集一次通吃。
    stub = wire(RecordQB())

    server.qb_set_share("test", hashes=["a" * 40], ratio=2.0)

    payload = stub.posts[0][1]
    assert payload["hashes"] == "a" * 40
    assert payload["ratioLimit"] == "2.0"
    assert payload["seedingTimeLimit"] == "-2"
    assert payload["inactiveSeedingTimeLimit"] == "-2"
    assert payload["shareLimitAction"] == "Stop"
    assert payload["shareLimitsMode"] == "Default"


def test_set_share_keeps_minutes_as_minutes(wire) -> None:
    # 源码里 seedingTimeLimit 是分钟（比较时 ×60），不能再按秒透传。
    stub = wire(RecordQB())

    server.qb_set_share("test", hashes=["a" * 40], seeding_time=60)

    payload = stub.posts[0][1]
    assert payload["seedingTimeLimit"] == "60"
    assert payload["ratioLimit"] == "-2"


def test_set_share_rejects_destructive_actions(wire) -> None:
    stub = wire(RecordQB())

    out = server.qb_set_share("test", hashes=["a" * 40], ratio=1.0, action="remove_with_content")

    assert out.startswith("ERROR:")
    assert "qb_remove" in out
    assert stub.posts == []


def test_set_share_warns_when_auto_management_overrides_the_limit(wire) -> None:
    stub = wire(
        RecordQB(
            torrents=[_torrent(auto_tmm=True, seeding_time_limit=-2)],
        )
    )

    out = server.qb_set_share("test", hashes=["a" * 40], seeding_time=60)

    assert "automatic torrent management" in out
    assert stub.posts[0][0] == "/api/v2/torrents/setShareLimits"


def test_set_share_flags_a_readback_mismatch(wire) -> None:
    wire(RecordQB(torrents=[_torrent(auto_tmm=False, seeding_time_limit=999)]))

    out = server.qb_set_share("test", hashes=["a" * 40], seeding_time=60)

    assert "reads back 999" in out


# -- qb_add ----------------------------------------------------------------


def test_add_accepts_a_magnet_link(wire) -> None:
    stub = wire(RecordQB())

    server.qb_add("test", "magnet:?xt=urn:btih:abc")

    path, data = stub.posts[0]
    assert path == "/api/v2/torrents/add"
    assert data["urls"] == "magnet:?xt=urn:btih:abc"
    assert data["paused"] == "true"


def test_add_accepts_an_http_url_with_extras(wire) -> None:
    stub = wire(RecordQB())

    server.qb_add("test", "https://example.com/a.torrent", savepath="/data", category="films")

    _path, data = stub.posts[0]
    assert data["savepath"] == "/data"
    assert data["category"] == "films"
    assert data["paused"] == "true"


def test_add_uploads_a_local_torrent_file(wire, tmp_path: Path) -> None:
    stub = wire(RecordQB())
    torrent = tmp_path / "sample.torrent"
    torrent.write_bytes(b"d8:announce4:http")

    server.qb_add("test", str(torrent), paused=False)

    _path, data = stub.posts[0]
    assert data["paused"] == "false"
    assert "files" in stub.post_kwargs[0]


def test_add_rejects_an_unreadable_source(wire) -> None:
    wire(RecordQB())

    out = server.qb_add("test", "/nope/missing.torrent")

    assert out.startswith("ERROR:")
    assert "not a magnet" in out


# -- qb_remove -------------------------------------------------------------


def test_remove_keeps_files_by_default(wire) -> None:
    stub = wire(RecordQB())

    out = server.qb_remove("test", ["a" * 40])

    path, data = stub.posts[0]
    assert path == "/api/v2/torrents/delete"
    assert data["deleteFiles"] == "false"
    assert "files kept" in out


def test_remove_can_delete_files_when_asked(wire) -> None:
    stub = wire(RecordQB())

    out = server.qb_remove("test", ["a" * 40], delete_files=True)

    assert stub.posts[0][1]["deleteFiles"] == "true"
    assert "including downloaded files" in out


def test_remove_requires_hashes(wire) -> None:
    wire(RecordQB())

    assert server.qb_remove("test", []).startswith("ERROR:")


# -- qb_category -----------------------------------------------------------


def test_category_sets_the_category(wire) -> None:
    stub = wire(RecordQB())

    server.qb_category("test", ["a" * 40], category="films")

    assert stub.posts[0] == (
        "/api/v2/torrents/setCategory",
        {"hashes": "a" * 40, "category": "films"},
    )


def test_category_adds_tags(wire) -> None:
    stub = wire(RecordQB())

    server.qb_category("test", ["a" * 40], tags=["a", "b"])

    assert stub.posts[0] == ("/api/v2/torrents/addTags", {"hashes": "a" * 40, "tags": "a,b"})


def test_category_requires_hashes(wire) -> None:
    wire(RecordQB())

    assert server.qb_category("test", []).startswith("ERROR:")


# -- error propagation -----------------------------------------------------


def test_write_tools_surface_client_errors(wire, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_name: str) -> RecordQB:
        raise QBError("connection refused")

    monkeypatch.setattr(server, "client", boom)

    assert server.qb_control("test", "pause").startswith("ERROR:")
    assert server.qb_set_limit("test", up=1).startswith("ERROR:")
    assert server.qb_remove("test", ["a" * 40]).startswith("ERROR:")


# -- 上传种子必须走 multipart（D5 的配套回归） ------------------------------


def test_add_uploads_the_torrent_file_as_multipart(wire, tmp_path) -> None:
    """只把文件塞进查询参数会让服务端收到空请求，qBittorrent 回 409。"""
    torrent = tmp_path / "sample.torrent"
    torrent.write_bytes(b"d4:infod4:name1:xe")
    stub = wire(RecordQB())

    server.qb_add("test", str(torrent), paused=True)

    path, _data = stub.posts[0]
    assert path == "/api/v2/torrents/add"
    assert stub.post_kwargs[0].get("files"), "torrent 文件必须以 files 字段上传"
    assert "torrents" in stub.post_kwargs[0]["files"]


def test_set_limit_per_torrent_reads_back_the_torrent_not_the_global(wire) -> None:
    stub = wire(
        RecordQB(
            transfer={"up_rate_limit": 0, "dl_rate_limit": 0},
            torrents=[_torrent(up_limit=1048576)],
        )
    )

    out = server.qb_set_limit("test", up=1048576, hashes=["a" * 40])

    assert not out.startswith("ERROR:"), out
    assert stub.posts[0][0] == "/api/v2/torrents/setUploadLimit"


# -- 备用限速（0.2.1）------------------------------------------------------


def test_set_limit_does_not_fail_when_alternative_limits_are_active(wire) -> None:
    """备用限速生效时，实际速率取备用值，全局读回对不上属正常，不能报错。"""
    wire(
        RecordQB(
            transfer=[
                {"up_rate_limit": 409600, "dl_rate_limit": 0},
                {"up_rate_limit": 409600, "dl_rate_limit": 0},
            ],
            prefs={"speed_limits_mode": 1, "alt_up_limit": 786432},
        )
    )

    out = server.qb_set_limit("test", up=1048576)

    assert not out.startswith("ERROR:"), out
    assert "alternative speed limits are active" in out


def test_set_limit_alternative_mode_writes_preferences(wire) -> None:
    stub = wire(
        RecordQB(
            transfer={"up_rate_limit": 0, "dl_rate_limit": 0},
            prefs={"alt_up_limit": 786432, "alt_dl_limit": 0},
        )
    )

    out = server.qb_set_limit("test", up=2097152, mode="alternative")

    assert not out.startswith("ERROR:"), out
    path, data = stub.posts[0]
    assert path == "/api/v2/app/setPreferences"
    assert '"alt_up_limit": 2097152' in data["json"]


def test_set_limit_alternative_mode_rejects_per_torrent(wire) -> None:
    wire(RecordQB())

    out = server.qb_set_limit("test", up=1, hashes=["a" * 40], mode="alternative")

    assert out.startswith("ERROR:")
    assert "global only" in out


def test_set_share_mode_maps_to_the_enum(wire) -> None:
    stub = wire(RecordQB())

    server.qb_set_share("test", hashes=["a" * 40], ratio=1.0, mode="all")

    assert stub.posts[0][1]["shareLimitsMode"] == "MatchAll"


def test_set_share_rejects_an_unknown_mode(wire) -> None:
    wire(RecordQB())

    assert server.qb_set_share("test", hashes=["a" * 40], ratio=1.0, mode="x").startswith(
        "ERROR:"
    )


def test_set_limit_rejects_an_unknown_mode(wire) -> None:
    wire(RecordQB())

    assert server.qb_set_limit("test", up=1, mode="turbo").startswith("ERROR:")


def test_set_limit_surfaces_a_non_numeric_limit(wire) -> None:
    wire(RecordQB())

    assert server.qb_set_limit("test", up="fast").startswith("ERROR:")


def test_set_limit_alternative_mode_covers_download_side(wire) -> None:
    stub = wire(RecordQB(transfer={"up_rate_limit": 0}, prefs={"alt_dl_limit": 0}))

    out = server.qb_set_limit("test", dl=1048576, mode="alternative")

    assert not out.startswith("ERROR:"), out
    assert '"alt_dl_limit": 1048576' in stub.posts[0][1]["json"]


def test_set_limit_alternative_mode_reports_a_readback_mismatch(wire) -> None:
    stub = wire(RecordQB(transfer={"up_rate_limit": 0}, prefs={"alt_up_limit": 999}))
    stub.apply_set_preferences = False  # 模拟服务端吞掉写入

    out = server.qb_set_limit("test", up=2097152, mode="alternative")

    assert out.startswith("ERROR:")
    assert "alternative limit did not take effect" in out


def test_set_limit_per_torrent_download_mismatch_is_reported(wire) -> None:
    wire(RecordQB(torrents=[_torrent(dl_limit=1)]))

    out = server.qb_set_limit("test", dl=1048576, hashes=["a" * 40])

    assert "download limit reads back" in out


def test_control_reannounce_and_force_start_paths(wire) -> None:
    stub = wire(RecordQB(torrents=[_torrent()]))

    server.qb_control("test", "reannounce")

    assert stub.posts[0][0] == "/api/v2/torrents/reannounce"


def test_add_warns_when_the_state_is_not_stopped(wire, tmp_path) -> None:
    torrent = tmp_path / "x.torrent"
    torrent.write_bytes(b"d4:infod4:name1:xe")
    stub = wire(RecordQB())
    calls = {"n": 0}

    def _torrents(**_kw):
        calls["n"] += 1
        return [] if calls["n"] == 1 else [{"hash": "b" * 40, "state": "downloading"}]

    stub.torrents = _torrents  # type: ignore[assignment]

    out = server.qb_add("test", str(torrent), paused=True)

    assert "may have been ignored" in out


def test_detail_falls_back_to_the_torrent_row_for_state(wire) -> None:
    """5.x 的 torrents/properties 不含 state，必须从种子列表里取。"""
    wire(
        RecordQB(
            props={"name": "No State Here"},
            torrents=[_torrent(state="forcedUP")],
        )
    )

    out = server.qb_detail("test", hash="a" * 40)

    assert "强制做种" in out
    assert "unknown" not in out
