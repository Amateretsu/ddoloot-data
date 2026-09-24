"""Canned-response adapter for the Page Store's transport seam (tests only).

Also a fake Playwright, so the browser adapter runs with no browser and no network.
"""

from __future__ import annotations

import types
from typing import Callable, Dict, List, Union

from page_store import Response

Reply = Union[Response, Exception]

CHALLENGE = Response(status=202, text="", headers={"x-amzn-waf-action": "challenge"})


def ok(html: str) -> Response:
    return Response(status=200, text=html)


class CannedTransport:
    """Plays the wiki from a script: per-URL queues of replies, then a fallback.

    Each reply is a :class:`Response` or an exception to raise. When a URL's queue is
    empty, *default* decides (a function of the URL); without one, the URL gets a 404.
    ``requests`` lists every URL fetched, in order.
    """

    def __init__(self, default: Callable[[str], Reply] | None = None) -> None:
        self.script: Dict[str, List[Reply]] = {}
        self.default = default
        self.requests: List[str] = []
        self.closed = False

    def reply(self, url: str, *replies: Reply) -> CannedTransport:
        self.script.setdefault(url, []).extend(replies)
        return self

    def fetch(self, url: str) -> Response:
        self.requests.append(url)
        queue = self.script.get(url)
        if queue:
            reply = queue.pop(0)
        elif self.default is not None:
            reply = self.default(url)
        else:
            reply = Response(status=404, text="not found")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self) -> None:
        self.closed = True


# ── A fake Playwright, for the browser adapter ───────────────────────────────


class _FakeRequest:
    def __init__(self, frame: object, navigation: bool) -> None:
        self.frame = frame
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class _FakeResponse:
    def __init__(
        self,
        status: int,
        request: _FakeRequest,
        url: str = "",
        headers: Dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.request = request
        self.url = url
        self.headers = headers or {}


class FakeBrowserPage:
    """Plays the wiki in a "browser": each URL loads a script of documents in turn.

    ``documents[url]`` is a list of ``(status, html)`` or ``(status, html, headers)``: the
    document ``goto`` loads, then each reload the page makes by itself (a WAF challenge
    clearing). Every document fires
    a main-frame navigation response; after the last one, the challenge script's own
    response fires too (not a navigation), as a real challenge page's would. A 202
    document carries ``x-amzn-waf-action: challenge``, as the wiki's does. ``goto``
    returns the first response, as Playwright's does. The content is the last document.
    ``waits`` counts the ``wait_for_selector`` calls (each one a timeout-long wait live).
    """

    main_frame = object()

    def __init__(self) -> None:
        self.documents: Dict[str, List[tuple]] = {}
        self.visited: List[str] = []
        self.waits = 0
        self._listeners: List[Callable[[_FakeResponse], None]] = []
        self._html = ""

    def route(self, _pattern: str, _handler: Callable) -> None:
        pass

    def on(self, event: str, listener: Callable[[_FakeResponse], None]) -> None:
        if event == "response":
            self._listeners.append(listener)

    def goto(self, url: str, **_options: object) -> _FakeResponse:
        self.visited.append(url)
        responses = []
        for status, html, *extra in self.documents[url]:
            headers = {"x-amzn-waf-action": "challenge"} if status == 202 else {}
            headers.update(*extra)
            request = _FakeRequest(self.main_frame, navigation=True)
            responses.append(_FakeResponse(status, request, url, headers))
            self._fire(responses[-1])
            self._html = html
        self._fire(_FakeResponse(200, _FakeRequest(self.main_frame, navigation=False)))
        return responses[0]

    def wait_for_selector(self, selector: str, **_options: object) -> None:
        self.waits += 1
        if selector.lstrip("#") not in self._html:
            raise TimeoutError(f"{selector} never appeared")

    def content(self) -> str:
        return self._html

    def _fire(self, response: _FakeResponse) -> None:
        for listener in self._listeners:
            listener(response)


def fake_playwright_module(page: FakeBrowserPage) -> types.ModuleType:
    """A stand-in for ``playwright.sync_api`` whose browser has the one *page*."""
    browser = types.SimpleNamespace(new_page=lambda **_: page, close=lambda: None)
    playwright = types.SimpleNamespace(
        chromium=types.SimpleNamespace(launch=lambda: browser), stop=lambda: None
    )
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: types.SimpleNamespace(start=lambda: playwright)
    return module
