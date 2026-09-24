"""ddo_sync — DDO Wiki update-page scrape queue and sync orchestration.

Public API:

    DDOSyncer         — top-level sync orchestrator
    JsonItemWriter    — writes each Scraped Item as JSON
    QueueRepository   — SQLite scrape queue (update pages + items)
    WikiApiClient     — MediaWiki Action API thin client
    UpdatePageParser  — HTML parser for item links on update pages
    sample_pages      — stratified sample of queued item pages into the Page Store

Models:

    ItemLink, QueueItem, QueueStats, UpdatePageStatus, SyncStatus

Exceptions:

    DDOSyncError, UpdatePageError, WikiApiError, QueueDbError, QueueSchemaError

Example:

    >>> from ddo_sync import DDOSyncer, JsonItemWriter, QueueRepository
    >>> from page_store import PageStore, load_scraper_config
    >>> with (
    ...     PageStore(load_scraper_config()) as store,
    ...     QueueRepository("queue.db") as queue_repo,
    ... ):
    ...     writer = JsonItemWriter(Path("cache/extracted"))
    ...     syncer = DDOSyncer(store, writer, queue_repo)
    ...     syncer.register_update_page("Update_5_named_items")
    ...     status = syncer.sync_all()
"""

from ddo_sync.exceptions import (
    DDOSyncError,
    QueueDbError,
    QueueSchemaError,
    UpdatePageError,
    WikiApiError,
)
from ddo_sync.item_writer import JsonItemWriter
from ddo_sync.models import (
    ItemLink,
    QueueItem,
    QueueStats,
    SyncStatus,
    UpdatePageStatus,
)
from ddo_sync.page_discovery import UpdatePageDiscoverer
from ddo_sync.protocols import (
    PageStoreProtocol,
    QueueRepositoryProtocol,
    ScrapedItemWriterProtocol,
    UpdatePageParserProtocol,
    WikiApiClientProtocol,
)
from ddo_sync.queue_db import QueueRepository
from ddo_sync.sampler import SampledPage, sample_pages
from ddo_sync.scrape_queue_db import ScrapeQueueRepository
from ddo_sync.syncer import DDOSyncer
from ddo_sync.update_page_db import UpdatePageRepository
from ddo_sync.update_page_parser import UpdatePageParser
from ddo_sync.wiki_api import WikiApiClient

__all__ = [
    "DDOSyncError",
    "DDOSyncer",
    "ItemLink",
    "JsonItemWriter",
    "PageStoreProtocol",
    "QueueDbError",
    "QueueItem",
    "QueueRepository",
    "QueueRepositoryProtocol",
    "QueueSchemaError",
    "QueueStats",
    "SampledPage",
    "ScrapeQueueRepository",
    "ScrapedItemWriterProtocol",
    "SyncStatus",
    "UpdatePageDiscoverer",
    "UpdatePageError",
    "UpdatePageParser",
    "UpdatePageParserProtocol",
    "UpdatePageRepository",
    "UpdatePageStatus",
    "WikiApiClient",
    "WikiApiClientProtocol",
    "WikiApiError",
    "sample_pages",
]
