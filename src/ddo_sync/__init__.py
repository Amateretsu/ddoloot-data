"""ddo_sync — DDO Wiki discovery, the crawl queue and sync orchestration.

Public API:

    DDOSyncer             — top-level sync orchestrator
    JsonItemWriter        — writes each Scraped Item under cache/extracted/<update>/
    QueueRepository       — the queue module: SQLite update pages + scrape queue
    discover_update_pages — named-items index page -> update page names
    read_update_page      — update page -> item links and revision id
    update_slug           — "Update_8_named_items" -> "update-8"
    sample_pages          — stratified sample of queued item pages into the Page Store

Models:

    ItemLink, QueueItem, QueueStats, UpdatePage, UpdatePageStatus, SyncStatus

Exceptions:

    DDOSyncError, UpdatePageError, QueueDbError, QueueSchemaError

Example:

    >>> from ddo_sync import DDOSyncer, JsonItemWriter, QueueRepository
    >>> from ddo_sync import discover_update_pages
    >>> from page_store import PageStore, load_scraper_config
    >>> with (
    ...     PageStore(load_scraper_config()) as store,
    ...     QueueRepository("queue.db") as queue_repo,
    ... ):
    ...     syncer = DDOSyncer(store, JsonItemWriter(Path("cache/extracted")), queue_repo)
    ...     for name in discover_update_pages(store):
    ...         syncer.register_update_page(name)
    ...     status = syncer.sync_all()
"""

from ddo_sync.discovery import (
    NAMED_ITEMS_INDEX_URL,
    UpdatePage,
    discover_update_pages,
    read_update_page,
    update_page_url,
    update_slug,
)
from ddo_sync.exceptions import (
    DDOSyncError,
    QueueDbError,
    QueueSchemaError,
    UpdatePageError,
)
from ddo_sync.item_writer import JsonItemWriter
from ddo_sync.models import (
    ItemLink,
    QueueItem,
    QueueStats,
    SyncStatus,
    UpdatePageStatus,
)
from ddo_sync.protocols import PageStoreProtocol, ScrapedItemWriterProtocol
from ddo_sync.queue_db import QueueRepository
from ddo_sync.sampler import SampledPage, sample_pages
from ddo_sync.syncer import DDOSyncer

__all__ = [
    "NAMED_ITEMS_INDEX_URL",
    "DDOSyncError",
    "DDOSyncer",
    "ItemLink",
    "JsonItemWriter",
    "PageStoreProtocol",
    "QueueDbError",
    "QueueItem",
    "QueueRepository",
    "QueueSchemaError",
    "QueueStats",
    "SampledPage",
    "ScrapedItemWriterProtocol",
    "SyncStatus",
    "UpdatePage",
    "UpdatePageError",
    "UpdatePageStatus",
    "discover_update_pages",
    "read_update_page",
    "sample_pages",
    "update_page_url",
    "update_slug",
]
