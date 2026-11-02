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
        props: dict[str, Any] | None = None,
        trackers: list[dict[str, Any]] | None = None,
    ) -> None:
        self._version = version
        self._torrents = torrents if torrents is not None else []
        self._transfer = transfer if transfer is not None else {}
        self._props = props if props is not None else {}
        self._trackers = trackers if trackers is not None else []
        self.posts: list[tuple[str, Any]] = []
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.torrent_calls: list[dict[str, Any]] = []

    def version(self) -> str:
        return self._version

    def torrents(self, **params: Any) -> list[dict[str, Any]]:
        self.torrent_calls.append(params)
        return self._torrents

    def transfer_info(self) -> dict[str, Any]:
        return self._transfer

    def post(self, path: str, data: Any = None, **_kwargs: Any) -> None:
        self.posts.append((path, data))

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


# -- qb_control ------------------------------------------------------------


def test_control_pause_hits_the_pause_endpoint(wire) -> None:
    stub = wire(RecordQB(torrents=[_torrent()]))

    out = server.qb_control("test", "pause")

    assert stub.posts[0][0] == "/api/v2/torrents/pause"
    assert stub.posts[0][1] == {"hashes": "a" * 40}
    assert "applied to 1 torrent" in out


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


def test_set_limit_global_posts_to_transfer_endpoints(wire) -> None:
    stub = wire(RecordQB(transfer={"up_limit": 0, "dl_limit": 0}))

    server.qb_set_limit("test", up=1024, dl=2048)

    paths = [p for p, _ in stub.posts]
    assert paths == ["/api/v2/transfer/uploadLimit", "/api/v2/transfer/downloadLimit"]
    assert stub.posts[0][1] == {"limit": "1024"}


def test_set_limit_per_torrent_posts_to_torrent_endpoints(wire) -> None:
    stub = wire(RecordQB(transfer={"up_limit": 0, "dl_limit": 0}))

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


def test_set_share_ratio_only_fills_in_unlimited_seeding_time(wire) -> None:
    stub = wire(RecordQB())

    server.qb_set_share("test", hashes=["a" * 40], ratio=2.0)

    assert stub.posts[0][1] == {"hashes": "a" * 40, "ratioLimit": "2.0", "seedingTimeLimit": "-2"}


def test_set_share_time_only_fills_in_unlimited_ratio(wire) -> None:
    stub = wire(RecordQB())

    server.qb_set_share("test", hashes=["a" * 40], seeding_time=86400)

    assert stub.posts[0][1] == {"hashes": "a" * 40, "seedingTimeLimit": "86400", "ratioLimit": "-2"}


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
    assert "files" in data


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
