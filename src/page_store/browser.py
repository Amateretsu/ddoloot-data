"""Browser adapter for the Page Store's transport seam (ADR 0006 fallback).

Loads pages in a real Chromium through Playwright, with the same user agent and pace as
plain fetching, no proxies and no CAPTCHA services. Playwright is the optional extra
``ddoloot-data[browser]`` and is imported only when the first page is fetched, so the
package works without it.

A page whose content never appears (the WAF challenge did not clear) comes back as a
``202`` response with ``x-amzn-waf-action: challenge``, so the Page Store's one challenge
check covers both adapters.
"""

from __future__ import annotations

from typing import Any

from page_store.errors import RunStoppedError
from page_store.transport import Response, TransportError

#: Element every rendered MediaWiki article has; absent on a challenge page.
_CONTENT_SELECTOR = "#mw-content-text"


class BrowserTransport:
    """Real-browser adapter; starts Chromium lazily on the first :meth:`fetch`."""

    def __init__(self, user_agent: str, timeout_seconds: float) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout_seconds * 1000
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def fetch(self, url: str) -> Response:
        page = self._ensure_page()
        try:
            resp = page.goto(
                url, wait_until="domcontentloaded", timeout=self._timeout_ms
            )
        except Exception as exc:  # playwright raises its own error types
            raise TransportError(f"browser could not load {url}: {exc}") from exc
        if resp is not None and resp.status == 404:
            return Response(status=404, text=page.content())
        try:
            page.wait_for_selector(_CONTENT_SELECTOR, timeout=self._timeout_ms)
        except Exception:
            return Response(
                status=202,
                text=page.content(),
                headers={"x-amzn-waf-action": "challenge"},
            )
        return Response(status=200, text=page.content())

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = self._browser = self._page = None

    def _ensure_page(self) -> Any:
        if self._page is None:
            try:
                from playwright.sync_api import (  # noqa: PLC0415 - optional
                    sync_playwright,
                )
            except ImportError as exc:
                raise RunStoppedError(
                    "the browser fallback needs Playwright: "
                    'pip install -e ".[browser]" && playwright install chromium'
                ) from exc
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
            self._page = self._browser.new_page(user_agent=self._user_agent)
        return self._page
