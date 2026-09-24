"""Structural protocols (PEP 544) for ddo_sync dependencies.

Defines the minimal interface each collaborator must satisfy.  Using
``Protocol`` instead of concrete imports keeps ``DDOSyncer`` decoupled from
specific implementations and makes unit testing easier — any object with the
right methods will type-check correctly.

Example:
    >>> from ddo_sync.protocols import PageStoreProtocol
    >>> def process(store: PageStoreProtocol) -> str:
    ...     return store.get("https://ddowiki.com/page/Item:Sword").html
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Protocol, runtime_checkable

from ddo_sync.models import ItemLink, QueueItem, QueueStats, UpdatePageStatus
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

    Adapters: :class:`~ddo_sync.item_writer.JsonItemWriter` in production, an in-memory
    fake in tests.
    """

    def write(self, item: ScrapedItem, report: dict[str, Any]) -> None:
        """Persist *item* and the report ``extract()`` produced for it."""
        ...


@runtime_checkable
class QueueRepositoryProtocol(Protocol):
    """Manages update-page registration and the scrape queue."""

    # ── Update page management ────────────────────────────────────────────────

    def register_update_page(self, page_name: str, page_url: str) -> None: ...

    def mark_page_synced(self, page_name: str, synced_at: datetime) -> None: ...

    def set_wiki_modified_at(
        self, page_name: str, modified_at: Optional[datetime]
    ) -> None: ...

    def get_update_page_status(self, page_name: str) -> Optional[UpdatePageStatus]: ...

    def list_update_pages(self) -> List[UpdatePageStatus]: ...

    # ── Queue writes ──────────────────────────────────────────────────────────

    def enqueue_items(self, links: List[ItemLink]) -> int: ...

    def mark_in_progress(self, item_id: int, started_at: datetime) -> None: ...

    def mark_complete(self, item_id: int, completed_at: datetime) -> None: ...

    def mark_failed(
        self, item_id: int, completed_at: datetime, error_message: str
    ) -> None: ...

    def mark_skipped(self, item_id: int) -> None: ...

    def reset_failed_to_pending(self, max_retries: int) -> int: ...

    # ── Queue reads ───────────────────────────────────────────────────────────

    def get_pending_items(self, limit: Optional[int] = None) -> List[QueueItem]: ...

    def get_queue_stats(self) -> QueueStats: ...

    def get_items_for_update_page(self, page_name: str) -> List[QueueItem]: ...


@runtime_checkable
class WikiApiClientProtocol(Protocol):
    """Queries the MediaWiki Action API."""

    def get_last_modified(self, page_name: str) -> Optional[datetime]:
        """Return the UTC timestamp of the last wiki revision, or ``None``.

        Args:
            page_name: Wiki page title, e.g. ``"Update_5_named_items"``.

        Returns:
            UTC :class:`~datetime.datetime`, or ``None`` if the page is missing.
        """
        ...


@runtime_checkable
class UpdatePageParserProtocol(Protocol):
    """Parses item links from update-page HTML."""

    def parse(self, html: str, page_name: str) -> List[ItemLink]:
        """Extract item links from *html*.

        Args:
            html:      Raw HTML of the update page.
            page_name: Page name used to populate :attr:`ItemLink.update_page`.

        Returns:
            Deduplicated list of :class:`~ddo_sync.models.ItemLink` objects.
        """
        ...
