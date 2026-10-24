"""Minimal qBittorrent WebAPI v2 client.

Only the endpoints qb-mcp needs are implemented. Authentication is
cookie-based (``SID``), matching the official WebUI API.

Three quirks are handled explicitly because they bite real deployments:

1. ``Referer``/``Origin`` must match the request ``Host`` exactly, otherwise
   qBittorrent rejects the login.
2. A failed login still returns HTTP 200 - the body is ``Fails.`` instead of
   ``Ok.``, so the body must be inspected.
3. Some reverse proxies (QNAP QTS in particular) return **204 No Content**
   with an empty body on success, so the status check must be ``< 400``
   rather than ``== 200``.
"""

from __future__ import annotations

from typing import Any

import requests

from .config import InstanceConfig

LOGIN_PATH = "/api/v2/auth/login"


class QBError(RuntimeError):
    """Raised for qBittorrent API failures."""


class QBClient:
    """One authenticated session against a single qBittorrent instance."""

    def __init__(self, instance: InstanceConfig) -> None:
        self.instance = instance
        self._session: requests.Session | None = None

    # -- session management -------------------------------------------------

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        base = self.instance.base_url
        # Quirk 1: Referer and Origin must match the Host used in the query.
        session.headers.update(
            {
                "Referer": base + "/",
                "Origin": base,
                "User-Agent": "qb-mcp/0.1",
            }
        )
        session.verify = self.instance.verify_ssl
        return session

    def _login(self) -> requests.Session:
        session = self._new_session()
        base = self.instance.base_url
        try:
            response = session.post(
                base + LOGIN_PATH,
                data={
                    "username": self.instance.username,
                    "password": self.instance.password,
                },
                timeout=self.instance.timeout,
            )
        except requests.RequestException as exc:
            raise QBError(f"{self.instance.display}: cannot reach {base}: {exc}") from exc

        # Quirks 2 and 3: 200 with "Fails." means bad credentials,
        # while a proxy may return 204 with an empty body on success.
        body = (response.text or "").strip()
        if response.status_code == 403:
            raise QBError(
                f"{self.instance.display}: login blocked - too many failed attempts "
                "(qBittorrent bans the source IP temporarily)"
            )
        if response.status_code >= 400:
            raise QBError(
                f"{self.instance.display}: login failed with HTTP {response.status_code}"
            )
        if body and body.lower() != "ok.":
            raise QBError(f"{self.instance.display}: login rejected ({body})")
        return session

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = self._login()
        return self._session

    def reset(self) -> None:
        """Drop the cached session so the next call logs in again."""
        self._session = None

    # -- transport ----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: Any = None,
        retry: bool = True,
    ) -> Any:
        """Perform a request, re-authenticating once on 401/403."""
        url = self.instance.base_url + path
        try:
            response = self.session.request(
                method, url, params=params, data=data, timeout=self.instance.timeout
            )
        except requests.RequestException as exc:
            raise QBError(f"{self.instance.display}: request to {path} failed: {exc}") from exc

        if response.status_code in (401, 403) and retry:
            self.reset()
            try:
                response = self.session.request(
                    method, url, params=params, data=data, timeout=self.instance.timeout
                )
            except requests.RequestException as exc:
                raise QBError(
                    f"{self.instance.display}: retry of {path} failed: {exc}"
                ) from exc

        if response.status_code >= 400:
            raise QBError(
                f"{self.instance.display}: {path} returned HTTP {response.status_code}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, data: Any = None, **params: Any) -> Any:
        return self.request("POST", path, params=params or None, data=data)

    # -- convenience --------------------------------------------------------

    def version(self) -> str:
        value = self.get("/api/v2/app/version")
        return str(value).strip()

    def build_info(self) -> dict[str, Any]:
        value = self.get("/api/v2/app/buildInfo")
        return value if isinstance(value, dict) else {}

    def preferences(self) -> dict[str, Any]:
        value = self.get("/api/v2/app/preferences")
        return value if isinstance(value, dict) else {}

    def server_state(self) -> dict[str, Any]:
        """Global stats from ``sync/maindata``.

        ``transfer/info`` only exposes live speeds and rate limits; the
        cumulative totals and global ratio live in ``server_state``.
        """
        value = self.get("/api/v2/sync/maindata")
        if isinstance(value, dict):
            state = value.get("server_state")
            if isinstance(state, dict):
                return state
        return {}

    def transfer_info(self) -> dict[str, Any]:
        value = self.get("/api/v2/transfer/info")
        return value if isinstance(value, dict) else {}

    def torrents(self, **params: Any) -> list[dict[str, Any]]:
        value = self.get("/api/v2/torrents/info", **params)
        return value if isinstance(value, list) else []

    def trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        value = self.get("/api/v2/torrents/trackers", hash=torrent_hash)
        return value if isinstance(value, list) else []
