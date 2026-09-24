"""page_store: the Page Store module (see ``CONTEXT.md``).

The local copy of wiki pages the catalog build reads from. It fetches a page only when
it is not already held, following the ADR 0006 acquisition policy configured in
``config/scraper.yaml``.

Interface::

    from page_store import PageStore, load_scraper_config

    with PageStore(load_scraper_config()) as store:
        page = store.get("https://ddowiki.com/page/Item:Breaker_of_Bodies")
        for cached in store.iter_cached():
            ...

The transport is the seam: ``HttpTransport`` (plain) and ``BrowserTransport`` (optional
extra ``ddoloot-data[browser]``) in production, canned responses in tests.
"""

from page_store.config import (
    MIN_CRAWL_DELAY_SECONDS,
    BrowserPolicy,
    ScraperConfig,
    ScraperConfigError,
    load_scraper_config,
)
from page_store.errors import (
    ChallengeError,
    FetchError,
    PageStoreError,
    RunStoppedError,
)
from page_store.store import RATIO_MIN_SAMPLE, CachedPage, PageStore
from page_store.transport import HttpTransport, Response, Transport, TransportError

__all__ = [
    "MIN_CRAWL_DELAY_SECONDS",
    "RATIO_MIN_SAMPLE",
    "BrowserPolicy",
    "CachedPage",
    "ChallengeError",
    "FetchError",
    "HttpTransport",
    "PageStore",
    "PageStoreError",
    "Response",
    "RunStoppedError",
    "ScraperConfig",
    "ScraperConfigError",
    "Transport",
    "TransportError",
    "load_scraper_config",
]
