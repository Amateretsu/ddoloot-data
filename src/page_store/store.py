"""The Page Store module: the local copy of wiki pages the catalog build reads from.

Interface: :meth:`PageStore.get` and :meth:`PageStore.iter_cached`, returning
:class:`CachedPage`. Behind it the module owns the whole ADR 0006 acquisition policy:

* one page at a time, paced at the crawl delay (at least 4 s, or robots.txt's larger
  ``Crawl-delay``), retries included;
* robots.txt (RFC 9309 matching), read once per run through the plain transport with the
  configured identity;
* retries for connection errors, timeouts, HTTP 429 and 5xx, up to ``max_retries``;
* WAF challenge detection (HTTP 202 or an ``x-amzn-waf-action`` header), never cached;
* browser fallback: a challenged page is retried through the browser adapter, and after
  ``consecutive_challenges`` challenged plain fetches in a row, or more than
  ``challenge_ratio`` of plain fetches challenged (once at least
  ``RATIO_MIN_SAMPLE`` plain fetches were made), the rest of the run uses the browser;
* with the browser disabled, or still challenged in the browser, :class:`ChallengeError`
  stops the run;
* the on-disk cache: readable-slug-plus-hash filenames and ``index.json``.

The transport is the seam: :class:`~page_store.transport.HttpTransport` and
:class:`~page_store.browser.BrowserTransport` in production, canned responses in tests.
A page already in the store is returned without any request; ``refresh=True`` is the only
way to refetch it (no TTL).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator, Literal, Optional

from loguru import logger

from page_store._cache import CacheIndex, page_title
from page_store._robots import RobotsRules
from page_store.browser import BrowserTransport
from page_store.config import MIN_CRAWL_DELAY_SECONDS, ScraperConfig
from page_store.errors import ChallengeError, FetchError, RunStoppedError
from page_store.transport import HttpTransport, Response, Transport, TransportError

Via = Literal["plain", "browser"]

#: Plain fetches needed before the challenge *ratio* may switch a run to the browser.
#: Below this, only the consecutive-challenge rule applies.
RATIO_MIN_SAMPLE = 10

_ROBOTS_URL = "https://ddowiki.com/robots.txt"


@dataclass(frozen=True)
class CachedPage:
    """One wiki page as held by the Page Store.

    Attributes:
        html: The page HTML as served.
        url: The URL it was fetched from.
        title: Wiki page title, e.g. ``Item:Breaker of Bodies``.
        fetched_at: When it was fetched (UTC).
        via: ``"plain"`` or ``"browser"``: which transport fetched it.
    """

    html: str
    url: str
    title: str
    fetched_at: datetime
    via: Via


class PageStore:
    """Local copy of wiki pages; fetches a page only when it is not already held.

    Args:
        config: Scraper policy (``config/scraper.yaml``).
        transport: Plain-fetch adapter; defaults to :class:`HttpTransport`.
        browser: Browser adapter, used only when ``config.browser.enabled``; defaults to
            :class:`~page_store.browser.BrowserTransport` (Playwright, started lazily).
        sleep: Called to wait out the crawl delay; tests pass a recorder.

    Use as a context manager, or call :meth:`close`, to release the transports. One
    instance is one run: escalation to the browser never carries over to the next.
    """

    def __init__(
        self,
        config: ScraperConfig,
        transport: Optional[Transport] = None,
        browser: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._index = CacheIndex(config.cache_dir)
        self._plain = transport or HttpTransport(
            config.user_agent, config.timeout_seconds
        )
        self._browser: Optional[Transport] = None
        if config.browser.enabled:
            if browser is None:
                browser = BrowserTransport(config.user_agent, config.timeout_seconds)
            self._browser = browser
        self._sleep = sleep
        self._delay = max(MIN_CRAWL_DELAY_SECONDS, config.crawl_delay_seconds)
        self._last_request: Optional[float] = None
        self._robots: Optional[RobotsRules] = None
        self._robots_loaded = False
        self._plain_fetches = 0
        self._challenged = 0
        self._consecutive = 0
        self._browser_only = False

    # ── Interface ────────────────────────────────────────────────────────────

    def get(self, url: str, refresh: bool = False) -> CachedPage:
        """Return the page at *url*, fetching it only if it is not held (or *refresh*).

        Raises:
            ValueError: *url* is not a ``https://ddowiki.com/page/...`` URL.
            FetchError: This page could not be fetched; other pages may still work.
            RunStoppedError: The policy says stop the run (:class:`ChallengeError`, or
                robots.txt unreadable with ``robots_fail_open: false``).
        """
        title = page_title(url)
        if not refresh:
            entry = self._index.lookup(title)
            if entry is not None:
                return _to_page(title, entry, self._index.read(entry))
        html, via = self._fetch(url)
        entry = self._index.store(
            title,
            html,
            {
                "url": url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "via": via,
            },
        )
        logger.info(f"Fetched {title!r} ({len(html)} bytes, {via})")
        return _to_page(title, entry, html)

    def iter_cached(self) -> Iterator[CachedPage]:
        """Yield every page held, ordered by title. Never touches the network."""
        for title, entry in self._index.entries():
            if self._index.lookup(title) is None:
                logger.warning(f"index.json lists {title!r} but its file is missing")
                continue
            yield _to_page(title, entry, self._index.read(entry))

    def close(self) -> None:
        self._plain.close()
        if self._browser is not None:
            self._browser.close()

    def __enter__(self) -> PageStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── Acquisition policy ───────────────────────────────────────────────────

    def _fetch(self, url: str) -> tuple[str, Via]:
        self._check_robots(url)
        if self._browser_only:
            return self._fetch_in_browser(url), "browser"

        resp = self._request(self._plain, url)
        challenged = _is_challenge(resp)
        self._record(challenged)
        if not challenged:
            return resp.text, "plain"
        if self._browser is None:
            raise ChallengeError(
                f"WAF challenge on {url} and the browser fallback is disabled "
                "(browser.enabled in the scraper config); stopping the run.",
                url=url,
            )
        if self._should_switch_to_browser():
            self._browser_only = True
            logger.warning(
                f"{self._challenged} of {self._plain_fetches} plain fetches challenged "
                f"({self._consecutive} in a row); the rest of this run uses the browser."
            )
        else:
            logger.info(f"WAF challenge on {url}; retrying in the browser.")
        return self._fetch_in_browser(url), "browser"

    def _fetch_in_browser(self, url: str) -> str:
        assert self._browser is not None
        resp = self._request(self._browser, url)
        if _is_challenge(resp):
            raise ChallengeError(
                f"WAF challenge on {url} not cleared in the browser; stopping the run.",
                url=url,
            )
        return resp.text

    def _record(self, challenged: bool) -> None:
        self._plain_fetches += 1
        if challenged:
            self._challenged += 1
            self._consecutive += 1
        else:
            self._consecutive = 0

    def _should_switch_to_browser(self) -> bool:
        policy = self._config.browser
        if self._consecutive >= policy.consecutive_challenges:
            return True
        return (
            self._plain_fetches >= RATIO_MIN_SAMPLE
            and self._challenged / self._plain_fetches > policy.challenge_ratio
        )

    def _request(self, transport: Transport, url: str) -> Response:
        """One paced request with retries. Returns a 200 or a challenge response."""
        attempts = 1 + self._config.max_retries
        problem = ""
        status: Optional[int] = None
        for attempt in range(attempts):
            self._pace(self._delay * (2**attempt))
            try:
                resp = transport.fetch(url)
            except TransportError as exc:
                problem, status = str(exc), None
                logger.warning(f"{url}: {problem} (attempt {attempt + 1}/{attempts})")
                continue
            if _is_challenge(resp) or resp.status == 200:
                return resp
            status = resp.status
            if status == 429 or status >= 500:
                problem = f"HTTP {status}"
                logger.warning(f"{url}: {problem} (attempt {attempt + 1}/{attempts})")
                continue
            if status == 404:
                raise FetchError(f"page not found (404): {url}", url=url, status=404)
            raise FetchError(f"HTTP {status} for {url}", url=url, status=status)
        raise FetchError(
            f"gave up on {url} after {attempts} attempt(s): {problem}",
            url=url,
            status=status,
        )

    def _pace(self, gap: float) -> None:
        if self._last_request is not None:
            remaining = self._last_request + gap - time.monotonic()
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = time.monotonic()

    def _check_robots(self, url: str) -> None:
        if not self._config.respect_robots_txt:
            return
        if not self._robots_loaded:
            self._robots_loaded = True
            self._robots = self._load_robots()
        if self._robots is not None and not self._robots.allows(url):
            raise FetchError(f"robots.txt disallows {url}", url=url)

    def _load_robots(self) -> Optional[RobotsRules]:
        try:
            resp = self._request(self._plain, _ROBOTS_URL)
        except FetchError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                return None  # no robots.txt: everything is allowed
            return self._robots_unavailable(str(exc))
        if _is_challenge(resp):
            return self._robots_unavailable("WAF challenge")
        rules = RobotsRules(resp.text, self._config.user_agent)
        delay = rules.crawl_delay
        if delay is not None and delay > self._delay:
            self._delay = delay
            logger.info(
                f"robots.txt Crawl-delay {delay}s is above the configured delay"
            )
        return rules

    def _robots_unavailable(self, reason: str) -> None:
        if not self._config.robots_fail_open:
            raise RunStoppedError(
                f"robots.txt unavailable ({reason}) and robots_fail_open is false; "
                "stopping the run."
            )
        logger.warning(
            f"robots.txt unavailable ({reason}); continuing (robots_fail_open)."
        )


def _is_challenge(resp: Response) -> bool:
    return resp.status == 202 or resp.header("x-amzn-waf-action") is not None


def _to_page(title: str, entry: dict, html: str) -> CachedPage:
    return CachedPage(
        html=html,
        url=entry["url"],
        title=title,
        fetched_at=datetime.fromisoformat(entry["fetched_at"]),
        via=entry["via"],
    )
