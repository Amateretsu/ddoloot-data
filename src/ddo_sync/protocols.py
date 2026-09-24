"""Structural protocols (PEP 544) for ddo_sync dependencies.

Defines the minimal interface each collaborator must satisfy.  Using
``Protocol`` instead of concrete imports keeps ``DDOSyncer`` decoupled from
specific implementations and makes unit testing easier — any object with the
right methods will type-check correctly.

Example:
    >>> from ddo_sync.protocols import FetcherProtocol
    >>> def process(fetcher: FetcherProtocol) -> str:
    ...     return fetcher.fetch_url("https://ddowiki.com/page/Item:Sword")
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # Python < 3.8 (not a target, but be explicit)
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from ddo_sync.models import ItemLink, QueueItem, QueueStats, UpdatePageStatus


@runtime_checkable
class FetcherProtocol(Protocol):
    """Fetches raw HTML from a URL."""

    def fetch_url(self, url: str) -> str:
        """Return the HTML body of *url*.

        Args:
            url: Absolute URL to fetch.

        Returns:
            Raw HTML as a string.
        """
        ...


@runtime_checkable
class NormalizerProtocol(Protocol):
    """Parses raw item-page HTML into a structured DDO item."""

    def normalize(self, html: str, url: str) -> object:
        """Parse *html* fetched from *url* into a DDO item object.

        Args:
            html: Raw HTML of the item page.
            url:  Source URL (used for logging / slug extraction).

        Returns:
            A structured item object (e.g. ``DDOItem``).
        """
        ...


@runtime_checkable
class ItemRepositoryProtocol(Protocol):
    """Persists DDO item data."""

    def upsert(self, item: object) -> None:
        """Insert or update *item* in the backing store.

        Args:
            item: Item object returned by :class:`NormalizerProtocol`.
        """
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
