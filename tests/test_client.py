"""Tests for the qBittorrent client, focused on the three auth quirks."""

from __future__ import annotations

import pytest
from conftest import FakeResponse, FakeSession

from qb_mcp.client import QBClient, QBError
from qb_mcp.config import InstanceConfig


def client_with(monkeypatch, instance, response):
    qb = QBClient(instance)
    session = FakeSession(response)
    monkeypatch.setattr(qb, "_new_session", lambda: session)
    return qb, session


def test_new_session_sends_referer_and_origin_matching_host(instance):
    """Quirk 1: Referer/Origin must equal the request host, port included."""
    session = QBClient(instance)._new_session()
    assert session.headers["Referer"] == "http://127.0.0.1:8080/"
    assert session.headers["Origin"] == "http://127.0.0.1:8080"


def test_trailing_slash_in_url_is_normalised():
    instance_ = InstanceConfig(
        name="t", url="http://host:8080/", username="u", password="p"
    )
    session = QBClient(instance_)._new_session()
    assert session.headers["Referer"] == "http://host:8080/"
    assert session.headers["Origin"] == "http://host:8080"


def test_login_success_with_200_ok(monkeypatch, instance):
    qb, _ = client_with(monkeypatch, instance, FakeResponse(200, "Ok."))
    assert qb.session is not None


def test_login_success_with_204_empty_body(monkeypatch, instance):
    """Quirk 3: QNAP's reverse proxy answers 204 with an empty body on success."""
    qb, _ = client_with(monkeypatch, instance, FakeResponse(204, ""))
    assert qb.session is not None


def test_login_failure_returns_200_with_fails_body(monkeypatch, instance):
    """Quirk 2: a wrong password still returns HTTP 200, with body 'Fails.'."""
    qb, _ = client_with(monkeypatch, instance, FakeResponse(200, "Fails."))
    with pytest.raises(QBError, match="login rejected"):
        _ = qb.session


def test_login_403_reports_ip_ban(monkeypatch, instance):
    qb, _ = client_with(monkeypatch, instance, FakeResponse(403, "banned"))
    with pytest.raises(QBError, match="too many failed attempts"):
        _ = qb.session


def test_login_5xx_raises(monkeypatch, instance):
    qb, _ = client_with(monkeypatch, instance, FakeResponse(502, "bad gateway"))
    with pytest.raises(QBError, match="login failed with HTTP 502"):
        _ = qb.session


def test_login_posts_credentials(monkeypatch, instance):
    qb, session = client_with(monkeypatch, instance, FakeResponse(200, "Ok."))
    _ = qb.session
    call = session.calls[0]
    assert call["url"].endswith("/api/v2/auth/login")
    assert call["data"] == {"username": "admin", "password": "secret"}


class SequencedSession(FakeSession):
    """Returns queued responses in order, repeating the last one."""

    def __init__(self, responses):
        super().__init__(responses[-1])
        self.queue = list(responses)
        self.headers = {}
        self.verify = True
        self.calls = []

    def _next(self):
        if len(self.queue) > 1:
            return self.queue.pop(0)
        return self.queue[0]

    def post(self, url, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._next()

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._next()


def test_reauthenticates_once_on_401(monkeypatch, instance):
    qb = QBClient(instance)
    session = SequencedSession(
        [
            FakeResponse(200, "Ok."),  # login
            FakeResponse(401, "forbidden"),  # first attempt
            FakeResponse(200, "", {"ok": True}),  # retry succeeds
        ]
    )
    monkeypatch.setattr(qb, "_new_session", lambda: session)
    assert qb.get("/api/v2/app/version") == {"ok": True}
    assert qb._session is session


def test_reset_forces_new_login(monkeypatch, instance):
    qb = QBClient(instance)
    created = []

    def factory():
        session = FakeSession(FakeResponse(200, "Ok."))
        created.append(session)
        return session

    monkeypatch.setattr(qb, "_new_session", factory)
    first = qb.session
    qb.reset()
    assert qb._session is None
    second = qb.session
    assert second is not first
    assert len(created) == 2
