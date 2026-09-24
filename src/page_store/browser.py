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
was sent. So is each request let through to another host (without its query), and each
main-frame document's status and ``x-amzn-waf-action``. When a challenged load never shows
the article, one WARNING sums it up: documents seen, wiki requests used, the last
document's status and WAF action, and the browser's age. The HTML returned is the article
as the server rendered it, since the wiki's own scripts never run.

The status returned is the real one, so the Page Store treats it as on the plain path
(404 and other 4xx fail the page, 429 and 5xx are retried):

* the first document is not a WAF challenge (a ``202`` or an ``x-amzn-waf-action``
  header, the Page Store's definition): its status, body and headers come back at once,
  with no wait. A 403 block, a 405 CAPTCHA, a 429 or a 503 is reported as such, never as
  a challenge;
* it is a challenge: the adapter waits for the article. Once it appears, the final
  document's status is returned (the reload's, not the challenge's). If it never
  appears, the last document's status is returned when that document is neither a
  challenge nor a ``200`` (the reload got a 503 or a 403, say); otherwise a ``202`` with
  ``x-amzn-waf-action: challenge``, so the Page Store's one challenge check covers both
  adapters.
"""

from __future__ import annotations

import time
from typing import Any, Optional
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
_WAF_HEADER = "x-amzn-waf-action"


class BrowserTransport:
    """Real-browser adapter; starts Chromium lazily on the first :meth:`fetch`."""

    def __init__(self, user_agent: str, timeout_seconds: float) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout_seconds * 1000
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._wiki_requests = 0
        self._document: Any = None  # the main frame's latest navigation response
        self._documents_seen = 0
        self._started: Optional[float] = None  # when Chromium was launched

    def fetch(self, url: str) -> Response:
        page = self._ensure_page()
        self._wiki_requests = 0
        self._document = None
        self._documents_seen = 0
        try:
            resp = page.goto(
                url, wait_until="domcontentloaded", timeout=self._timeout_ms
            )
        except Exception as exc:  # playwright raises its own error types
            raise TransportError(f"browser could not load {url}: {exc}") from exc
        if resp is not None and not _is_challenge(resp):
            return _response(resp, page.content())  # no challenge: nothing to wait for
        try:
            page.wait_for_selector(_CONTENT_SELECTOR, timeout=self._timeout_ms)
        except Exception:
            last = self._document or resp
            self._log_no_article(url, last)
            if last is not None and not _is_challenge(last) and last.status != 200:
                return _response(last, page.content())
            return Response(
                status=202,
                text=page.content(),
                headers={_WAF_HEADER: "challenge"},
            )
        final = self._document or resp
        if final is None:
            return Response(status=200, text=page.content())
        return _response(final, page.content())

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
            self._page.on("response", self._on_response)
            self._started = time.monotonic()
        return self._page

    def _log_no_article(self, url: str, last: Any) -> None:
        age = time.monotonic() - self._started if self._started is not None else 0.0
        document = _describe(last) if last is not None else "none"
        logger.warning(
            f"no article for {url} after {self._timeout_ms / 1000:g}s: "
            f"{self._documents_seen} document(s) seen, {self._wiki_requests} of "
            f"{MAX_WIKI_REQUESTS} wiki requests used, last document {document}; "
            f"browser up {age:.0f}s (browser)"
        )

    def _on_response(self, response: Any) -> None:
        """Remember the response of each main-frame navigation (the page, its reload)."""
        request = response.request
        if request.is_navigation_request() and request.frame == self._page.main_frame:
            self._document = response
            self._documents_seen += 1
            logger.debug(f"document {response.url} -> {_describe(response)} (browser)")

    def _route(self, route: Any) -> None:
        """Let through non-wiki requests and up to two wiki ``/page/`` documents."""
        request = route.request
        parts = urlsplit(request.url)
        host = parts.hostname or ""
        if host != _WIKI_HOST and not host.endswith("." + _WIKI_HOST):
            logger.debug(
                f"pass {request.resource_type} {parts.scheme}://{host}{parts.path} (browser)"
            )
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


def _is_challenge(response: Any) -> bool:
    """A WAF challenge, as the Page Store defines it: 202 or an ``x-amzn-waf-action``."""
    return (
        response.status == 202 or (response.headers or {}).get(_WAF_HEADER) is not None
    )


def _response(document: Any, html: str) -> Response:
    """A playwright document response as the Page Store's :class:`Response`."""
    return Response(
        status=document.status, text=html, headers=dict(document.headers or {})
    )


def _describe(response: Any) -> str:
    """``<status>``, plus the WAF action header when the response carries one."""
    action = (response.headers or {}).get(_WAF_HEADER)
    return (
        f"{response.status}, {_WAF_HEADER}: {action}"
        if action
        else str(response.status)
    )
