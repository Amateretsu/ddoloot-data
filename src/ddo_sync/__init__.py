"""ddo_sync — DDO Wiki update-page scrape queue and sync orchestration.

Public API:

    DDOSyncer         — top-level sync orchestrator
    JsonItemWriter    — writes each Scraped Item as JSON
    QueueRepository   — SQLite scrape queue (update pages + items)
    WikiApiClient     — MediaWiki Action API thin client
    UpdatePageParser  — HTML parser for item links on update pages

Models:

    ItemLink, QueueItem, QueueStats, UpdatePageStatus, SyncStatus

Exceptions:

    DDOSyncError, UpdatePageError, WikiApiError, QueueDbError, QueueSchemaError

Example:

    >>> from ddo_sync import DDOSyncer, JsonItemWriter, QueueRepository
    >>> from ddowiki_scraper import WikiFetcher
    >>> with (
    ...     WikiFetcher(config) as fetcher,
    ...     QueueRepository("queue.db") as queue_repo,
    ... ):
    ...     writer = JsonItemWriter(Path("cache/extracted"))
    ...     syncer = DDOSyncer(fetcher, writer, queue_repo)
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
    FetcherProtocol,
    QueueRepositoryProtocol,
    ScrapedItemWriterProtocol,
    UpdatePageParserProtocol,
    WikiApiClientProtocol,
)
from ddo_sync.queue_db import QueueRepository
from ddo_sync.scrape_queue_db import ScrapeQueueRepository
from ddo_sync.syncer import DDOSyncer
from ddo_sync.update_page_db import UpdatePageRepository
from ddo_sync.update_page_parser import UpdatePageParser
from ddo_sync.wiki_api import WikiApiClient

__all__ = [
    "DDOSyncError",
    "DDOSyncer",
    "FetcherProtocol",
    "ItemLink",
    "JsonItemWriter",
    "QueueDbError",
    "QueueItem",
    "QueueRepository",
    "QueueRepositoryProtocol",
    "QueueSchemaError",
    "QueueStats",
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
]
