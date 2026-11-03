"""Transport-level tests for :mod:`qb_mcp.client`.

These cover the branches that a healthy qBittorrent never exercises: a dead
host, a rejected re-login, an empty 204 body and a response that is not JSON.
They are the lines that decide whether a failure surfaces as a clear QBError
or as a traceback in the user's face.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from qb_mcp.client import QBClient, QBError
from qb_mcp.config import InstanceConfig


class Resp:
    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        payload: Any = None,
        content: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        if content is None:
            content = b"x" if (payload is not None or text) else b""
        self.content = content
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class Scripted:
    """Feeds a fixed list of responses/exception to the client."""

    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)
        self.calls: list[str] = []
        self.verify = True

    def _next(self) -> Any:
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def post(self, _url: str, **_kwargs: Any) -> Resp:
        self.calls.append("post")
        return self._next()

    def request(self, _method: str, _url: str, **_kwargs: Any) -> Resp:
        self.calls.append("request")
        return self._next()


@pytest.fixture()
def client(instance: InstanceConfig, monkeypatch: pytest.MonkeyPatch):
    def _make(items: list[Any]) -> tuple[QBClient, Scripted]:
        scripted = Scripted(items)
        monkeypatch.setattr(QBClient, "_new_session", lambda _self: scripted)
        return QBClient(instance), scripted

    return _make


def test_login_reports_an_unreachable_host(client) -> None:
    qb, _ = client([requests.ConnectionError("boom")])

    with pytest.raises(QBError) as excinfo:
        qb.version()

    assert "cannot reach" in str(excinfo.value)


def test_request_reports_a_transport_failure(client) -> None:
    qb, _ = client([Resp(200, "Ok."), requests.ConnectionError("boom")])

    with pytest.raises(QBError) as excinfo:
        qb.version()

    assert "request to" in str(excinfo.value)


def test_retry_reports_a_transport_failure(client) -> None:
    qb, _ = client(
        [
            Resp(200, "Ok."),
            Resp(401, "Forbidden"),
            Resp(200, "Ok."),
            requests.ConnectionError("boom"),
        ]
    )

    with pytest.raises(QBError) as excinfo:
        qb.version()

    assert "retry of" in str(excinfo.value)


def test_unauthorised_response_triggers_one_relogin(client) -> None:
    qb, scripted = client(
        [Resp(200, "Ok."), Resp(401, "no"), Resp(200, "Ok."), Resp(200, payload={"ok": 1})]
    )

    assert qb.get("/api/v2/app/version") == {"ok": 1}
    assert scripted.calls == ["post", "request", "post", "request"]


def test_server_error_becomes_a_qberror(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(500, "boom", content=b"boom")])

    with pytest.raises(QBError) as excinfo:
        qb.version()

    assert "returned HTTP 500" in str(excinfo.value)


def test_empty_body_returns_none(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(204, "", content=b"")])

    assert qb.get("/api/v2/app/version") is None


def test_non_json_body_is_returned_as_text(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, "plain", content=b"plain")])

    assert qb.get("/api/v2/app/version") == "plain"


def test_version_strips_whitespace(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload="v5.2.4\n")])

    assert qb.version() == "v5.2.4"


def test_build_info_defaults_to_an_empty_dict(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload=["not", "a", "dict"])])

    assert qb.build_info() == {}


def test_preferences_defaults_to_an_empty_dict(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload="nope")])

    assert qb.preferences() == {}


def test_server_state_extracts_the_nested_dict(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload={"server_state": {"alltime_ul": 7}})])

    assert qb.server_state() == {"alltime_ul": 7}


def test_server_state_is_empty_when_the_shape_is_wrong(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload={"server_state": "bad"})])

    assert qb.server_state() == {}


def test_transfer_info_defaults_to_an_empty_dict(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload=[])])
    assert qb.transfer_info() == {}


def test_torrents_defaults_to_an_empty_list(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload={"not": "a list"})])
    assert qb.torrents() == []


def test_trackers_defaults_to_an_empty_list(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload={"not": "a list"})])
    assert qb.trackers("a" * 40) == []


def test_post_forwards_the_payload(client) -> None:
    qb, _ = client([Resp(200, "Ok."), Resp(200, payload=None, content=b"")])

    assert qb.post("/api/v2/torrents/delete", data={"hashes": "abc"}) is None
