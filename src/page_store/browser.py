"""Browser adapter for the Page Store's transport seam (ADR 0006 fallback).

Loads pages in a real, unmodified Chromium through Playwright, with the same user agent
and pace as plain fetching, and no proxies, stealth plugins, fingerprint spoofing or
CAPTCHA services. Playwright is the optional extra ``ddoloot-data[browser]`` and is
imported only when the first page is fetched, so the package works without it.

The browser may request only ``/page/`` documents from the wiki, and at most
:data:`MAX_WIKI_REQUESTS` per :meth:`BrowserTransport.fetch`: the challenged document and,
once the WAF challenge clears, its reload. Every other wiki request the page would make
(``load.php`` scripts and styles, images, ``api.php``) and any further reload is aborted
before it is sent, so a challenge that keeps reloading can never loop against the wiki.
Requests to other hosts (the AWS WAF challenge script and token service) go through.
Every wiki request is logged at DEBUG, so a ``--verbose`` run shows exactly what the wiki
was sent. The HTML returned is the article as the server rendered it, since the wiki's
own scripts never run.

A page whose content never appears (the WAF challenge did not clear) comes back as a
``202`` response with ``x-amzn-waf-action: challenge``, so the Page Store's one challenge
check covers both adapters.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from loguru import logger

from page_store.errors import RunStoppedError
from page_store.transport import Response, TransportError

#: Element every rendered MediaWiki article has; absent on a challenge page.
_CONTENT_SELECTOR = "#mw-content-text"

#: Wiki requests one :meth:`BrowserTransport.fetch` may send: the page and one reload.
MAX_WIKI_REQUESTS = 2

_WIKI_HOST = "ddowiki.com"
_PAGE_PREFIX = "/page/"


class BrowserTransport:
    """Real-browser adapter; starts Chromium lazily on the first :meth:`fetch`."""

    def __init__(self, user_agent: str, timeout_seconds: float) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout_seconds * 1000
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._wiki_requests = 0

    def fetch(self, url: str) -> Response:
        page = self._ensure_page()
        self._wiki_requests = 0
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
            self._page.route("**/*", self._route)
        return self._page

    def _route(self, route: Any) -> None:
        """Let through non-wiki requests and up to two wiki ``/page/`` documents."""
        request = route.request
        parts = urlsplit(request.url)
        host = parts.hostname or ""
        if host != _WIKI_HOST and not host.endswith("." + _WIKI_HOST):
            route.continue_()
            return
        is_page = request.resource_type == "document" and parts.path.startswith(
            _PAGE_PREFIX
        )
        if is_page and self._wiki_requests < MAX_WIKI_REQUESTS:
            self._wiki_requests += 1
            logger.debug(f"GET {request.url} (browser)")
            route.continue_()
            return
        logger.debug(f"blocked {request.resource_type} {request.url} (browser)")
        route.abort()
