"""Data transfer objects for the ddo_sync package.

All classes are frozen dataclasses — internal DTOs with no Pydantic validation.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Dict, Optional


@dataclasses.dataclass(frozen=True)
class ItemLink:
    """A named-item hyperlink discovered on an update page.

    Attributes:
        item_name:   Display name, e.g. "Sword of Shadow".
        wiki_url:    Absolute URL, e.g. "https://ddowiki.com/page/Item:Sword_of_Shadow".
        update_page: Page-name key of the update page, e.g. "Update_5_named_items".

    Example:
        >>> ItemLink(
        ...     item_name="Sword of Shadow",
        ...     wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow",
        ...     update_page="Update_5_named_items",
        ... )
    """

    item_name: str
    wiki_url: str
    update_page: str


@dataclasses.dataclass(frozen=True)
class QueueItem:
    """Full state of one row in the scrape_queue table.

    Attributes:
        id:            SQLite ROWID.
        item_name:     Name of the DDO item.
        wiki_url:      Full URL of the item's wiki page.
        update_page:   FK to update_pages.page_name.
        status:        One of: pending | in_progress | complete | failed | skipped.
        queued_at:     When the item was added to the queue.
        started_at:    When processing began, or None.
        completed_at:  When processing finished, or None.
        error_message: Last error string, or None.
        retry_count:   Number of failed processing attempts.
    """

    id: int
    item_name: str
    wiki_url: str
    update_page: str
    status: str
    queued_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
    retry_count: int


@dataclasses.dataclass(frozen=True)
class QueueStats:
    """Aggregate counts of scrape_queue rows by status.

    Attributes:
        pending:     Items waiting to be processed.
        in_progress: Items currently being processed.
        complete:    Items successfully scraped.
        failed:      Items that exhausted all retries.
        skipped:     Items deliberately skipped.

    Example:
        >>> stats = QueueStats(pending=10, in_progress=2, complete=47, failed=1, skipped=0)
        >>> stats.total
        60
    """

    pending: int
    in_progress: int
    complete: int
    failed: int
    skipped: int

    @property
    def total(self) -> int:
        return (
            self.pending + self.in_progress + self.complete + self.failed + self.skipped
        )


@dataclasses.dataclass(frozen=True)
class UpdatePageStatus:
    """Current sync state for one registered update page.

    Attributes:
        page_name:        Natural key, e.g. "Update_5_named_items".
        page_url:         Full URL of the update page.
        last_synced_at:   When we last fetched + parsed item links, or None.
        wiki_modified_at: MediaWiki API timestamp of the last wiki edit, or None.

    The ``needs_resync`` property is computed purely from the two timestamps —
    no external logic needed, and the derivation is transparent to readers.

    Example:
        >>> status = UpdatePageStatus(
        ...     page_name="Update_5_named_items",
        ...     page_url="https://ddowiki.com/page/Update_5_named_items",
        ...     last_synced_at=None,
        ...     wiki_modified_at=None,
        ... )
        >>> status.needs_resync
        True
    """

    page_name: str
    page_url: str
    last_synced_at: Optional[datetime]
    wiki_modified_at: Optional[datetime]

    @property
    def needs_resync(self) -> bool:
        """True when the wiki is newer than our last sync, or either timestamp is absent."""
        if self.last_synced_at is None or self.wiki_modified_at is None:
            return True
        return self.wiki_modified_at > self.last_synced_at


@dataclasses.dataclass(frozen=True)
class SyncStatus:
    """Top-level status snapshot returned by DDOSyncer.get_status().

    Attributes:
        queue_stats:  Aggregate item counts across all statuses.
        update_pages: Per-page sync state, keyed by page_name.

    Example:
        >>> status = syncer.get_status()
        >>> status.queue_stats.pending
        0
        >>> status.update_pages["Update_5_named_items"].needs_resync
        False
    """

    queue_stats: QueueStats
    update_pages: Dict[str, UpdatePageStatus]
