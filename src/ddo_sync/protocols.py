"""Seams (PEP 544 protocols) of the ddo_sync module.

A protocol exists only where two adapters do: the Page Store (real + in-memory fake) and
the Scraped Item writer (the committed catalog layout + in-memory fake). The queue,
discovery and the extractor have one implementation each and are used directly.

Example:
    >>> from ddo_sync.protocols import PageStoreProtocol
    >>> def process(store: PageStoreProtocol) -> str:
    ...     return store.get("https://ddowiki.com/page/Item:Sword").html
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from item_extractor import ScrapedItem
from page_store import CachedPage


@runtime_checkable
class PageStoreProtocol(Protocol):
    """Seam where the syncer reads wiki pages.

    Adapters: :class:`page_store.PageStore` in production (fetches only pages it does
    not hold, under the ADR 0006 policy), an in-memory fake in tests.
    """

    def get(self, url: str, refresh: bool = False) -> CachedPage:
        """Return the page at *url*; refetch it only when *refresh* is true.

        Raises:
            page_store.FetchError: This page could not be fetched.
            page_store.RunStoppedError: The acquisition policy says stop the run.
        """
        ...


@runtime_checkable
class ScrapedItemWriterProtocol(Protocol):
    """Seam where the syncer hands off each Scraped Item and its extraction report.

    Adapters: :class:`~ddo_sync.catalog_writer.CatalogWriter` in production, an in-memory
    fake in tests.
    """

    def write(self, item: ScrapedItem, report: dict[str, Any]) -> None:
        """Persist *item* and the report ``extract()`` produced for it."""
        ...
