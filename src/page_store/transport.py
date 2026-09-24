"""The transport seam of the Page Store, and its HTTP adapter.

A transport turns one URL into one :class:`Response`. It knows nothing about caching,
pacing, robots.txt, retries or challenges; the Page Store owns all of that. Adapters:

* :class:`HttpTransport` - plain ``requests`` fetch (production).
* :class:`page_store.browser.BrowserTransport` - a real browser (production fallback,
  optional extra ``ddoloot-data[browser]``).
* canned responses in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, runtime_checkable

import requests


class TransportError(Exception):
    """The request never produced an HTTP response (connection error, timeout...)."""


@dataclass(frozen=True)
class Response:
    """One HTTP response as a transport saw it. Header names are matched ignoring case."""

    status: int
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> Optional[str]:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


@runtime_checkable
class Transport(Protocol):
    """Fetches one URL. Raises :class:`TransportError` when there is no response."""

    def fetch(self, url: str) -> Response: ...

    def close(self) -> None: ...


class HttpTransport:
    """Plain HTTP adapter: one ``requests`` session with the configured identity."""

    def __init__(self, user_agent: str, timeout_seconds: float) -> None:
        self._user_agent = user_agent
        self._timeout = timeout_seconds
        self._session: Optional[requests.Session] = None

    def fetch(self, url: str) -> Response:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": self._user_agent,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
        try:
            resp = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        return Response(
            status=resp.status_code, text=resp.text, headers=dict(resp.headers)
        )

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
