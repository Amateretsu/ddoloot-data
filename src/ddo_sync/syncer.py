"""DDOSyncer — orchestrates the full DDO item sync pipeline.

Wires together WikiFetcher, WikiApiClient, UpdatePageParser, QueueRepository,
ItemNormalizer, and ItemRepository into a single cohesive sync cycle.

Example:
    >>> from ddo_sync import DDOSyncer
    >>> with WikiFetcher(config) as fetcher, \\
    ...      ItemRepository("loot.db") as item_repo, \\
    ...      QueueRepository("queue.db") as queue_repo:
    ...     syncer = DDOSyncer(fetcher, ItemNormalizer(), item_repo, queue_repo)
    ...     syncer.register_update_page("Update_5_named_items")
    ...     syncer.sync_all()
    ...     print(syncer.get_status())
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from loguru import logger

from ddo_sync.exceptions import UpdatePageError
from ddo_sync.models import ItemLink, SyncStatus
from ddo_sync.protocols import (
    FetcherProtocol,
    ItemRepositoryProtocol,
    NormalizerProtocol,
    QueueRepositoryProtocol,
    UpdatePageParserProtocol,
    WikiApiClientProtocol,
)
from ddo_sync.update_page_parser import UpdatePageParser
from ddo_sync.wiki_api import WikiApiClient

_BASE_URL = "https://ddowiki.com"
_PAGE_PREFIX = f"{_BASE_URL}/page/"


class DDOSyncer:
    """Orchestrates the full DDO item sync pipeline.

    All dependencies are injected so callers control lifecycle (context
    managers, session reuse, in-memory testing).  ``api_client`` and
    ``parser`` default to the standard implementations when omitted.

    Args:
        fetcher:     Object satisfying :class:`~ddo_sync.protocols.FetcherProtocol`.
        normalizer:  Object satisfying :class:`~ddo_sync.protocols.NormalizerProtocol`.
        item_repo:   Object satisfying :class:`~ddo_sync.protocols.ItemRepositoryProtocol`.
        queue_repo:  Object satisfying :class:`~ddo_sync.protocols.QueueRepositoryProtocol`.
        max_retries: Items that have failed this many times are not reset to
                     pending on the next cycle (default: 3).
        api_client:  Optional :class:`~ddo_sync.protocols.WikiApiClientProtocol`.
                     Defaults to :class:`~ddo_sync.wiki_api.WikiApiClient`.
        parser:      Optional :class:`~ddo_sync.protocols.UpdatePageParserProtocol`.
                     Defaults to :class:`~ddo_sync.update_page_parser.UpdatePageParser`.

    Example:
        >>> syncer = DDOSyncer(fetcher, normalizer, item_repo, queue_repo)
        >>> syncer.register_update_page("Update_5_named_items")
        >>> syncer.sync_all()
    """

    def __init__(
        self,
        fetcher: FetcherProtocol,
        normalizer: NormalizerProtocol,
        item_repo: ItemRepositoryProtocol,
        queue_repo: QueueRepositoryProtocol,
        max_retries: int = 3,
        api_client: Optional[WikiApiClientProtocol] = None,
        parser: Optional[UpdatePageParserProtocol] = None,
    ) -> None:
        self._fetcher = fetcher
        self._normalizer = normalizer
        self._item_repo = item_repo
        self._queue_repo = queue_repo
        self._max_retries = max_retries
        self._api_client: WikiApiClientProtocol = api_client or WikiApiClient()
        self._parser: UpdatePageParserProtocol = parser or UpdatePageParser(
            base_url=_BASE_URL
        )

    # ── Registration ─────────────────────────────────────────────────────────

    def register_update_page(self, page_name: str) -> None:
        """Register an update page for tracking. Safe to call multiple times.

        Derives the full URL from ``page_name`` using the standard wiki path
        and registers it with the queue. Spaces are converted to underscores.

        Args:
            page_name: Wiki page title, e.g. ``"Update_5_named_items"`` or
                       ``"Update 5 named items"`` (spaces accepted).

        Example:
            >>> syncer.register_update_page("Update_5_named_items")
            >>> syncer.register_update_page("Update_6_named_items")
        """
        normalized = page_name.replace(" ", "_")
        url = self._build_page_url(normalized)
        self._queue_repo.register_update_page(normalized, url)
        logger.info(f"Registered update page: {normalized!r}")

    # ── Sync orchestration ───────────────────────────────────────────────────

    def sync_all(self) -> SyncStatus:
        """Run a full sync cycle for all registered update pages.

        Steps:
          1. Reset failed items below ``max_retries`` back to pending.
          2. For each registered update page:
             a. Query MediaWiki API for the page's last-modified timestamp.
             b. Store ``wiki_modified_at`` in the queue database.
             c. If ``needs_resync`` is True: fetch HTML, parse links, enqueue.
          3. Process the full queue via :meth:`process_queue`.
          4. Return :meth:`get_status`.

        Returns:
            :class:`SyncStatus` snapshot after all processing completes.

        Example:
            >>> status = syncer.sync_all()
            >>> status.queue_stats.complete
            47
        """
        reset_count = self._queue_repo.reset_failed_to_pending(self._max_retries)
        if reset_count:
            logger.info(f"Reset {reset_count} failed items to pending")

        for page_status in self._queue_repo.list_update_pages():
            page_name = page_status.page_name
            self._refresh_wiki_timestamp(page_name)

            # Re-read status after updating the timestamp
            updated = self._queue_repo.get_update_page_status(page_name)
            if updated and updated.needs_resync:
                logger.info(f"Update page {page_name!r} needs re-sync — fetching")
                try:
                    self.sync_update_page(page_name)
                except UpdatePageError as exc:
                    logger.error(f"Failed to sync update page {page_name!r}: {exc}")

        self.process_queue()
        return self.get_status()

    def sync_update_page(self, page_name: str) -> List[ItemLink]:
        """Force a sync of one update page regardless of the resync check.

        Fetches the page HTML, parses item links, enqueues new items, and
        records ``last_synced_at``. Does **not** process the queue — call
        :meth:`process_queue` separately.

        Args:
            page_name: Natural key of the update page to sync.

        Returns:
            List of :class:`ItemLink` objects discovered on the page.

        Raises:
            UpdatePageError: If the page cannot be fetched or parsed.

        Example:
            >>> links = syncer.sync_update_page("Update_5_named_items")
            >>> len(links)
            12
        """
        normalized = page_name.replace(" ", "_")
        page_url = self._build_page_url(normalized)

        logger.info(f"Syncing update page: {normalized!r}")

        try:
            html = self._fetcher.fetch_url(page_url)
        except Exception as exc:
            raise UpdatePageError(
                f"Failed to fetch update page {normalized!r}: {exc}",
                page_url=page_url,
            ) from exc

        links = self._parser.parse(html, normalized)
        inserted = self._queue_repo.enqueue_items(links)
        self._queue_repo.mark_page_synced(normalized, _utcnow())

        logger.info(
            f"Synced {normalized!r}: {len(links)} items found, {inserted} newly queued"
        )
        return links

    def process_queue(self, limit: Optional[int] = None) -> Tuple[int, int]:
        """Process pending items from the scrape queue.

        For each pending item:
          1. Mark as ``in_progress``.
          2. Fetch item page HTML via :meth:`WikiFetcher.fetch_url`.
          3. Normalize HTML via :meth:`ItemNormalizer.normalize`.
          4. Upsert into :attr:`item_repo`.
          5. Mark as ``complete``.

        If any step raises, the item is marked as ``failed`` and the error
        message is stored. Other items continue processing.

        Args:
            limit: Maximum number of items to process. ``None`` means all
                   currently pending items.

        Returns:
            Tuple of ``(success_count, failure_count)``.

        Example:
            >>> success, failures = syncer.process_queue(limit=50)
        """
        pending = self._queue_repo.get_pending_items(limit=limit)
        if not pending:
            logger.debug("Queue: no pending items")
            return 0, 0

        logger.info(f"Processing {len(pending)} pending queue item(s)")
        success = 0
        failures = 0

        for queue_item in pending:
            now = _utcnow()
            self._queue_repo.mark_in_progress(queue_item.id, now)
            try:
                html = self._fetcher.fetch_url(queue_item.wiki_url)
                ddo_item = self._normalizer.normalize(html, queue_item.wiki_url)
                self._item_repo.upsert(ddo_item)
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                self._queue_repo.mark_failed(queue_item.id, _utcnow(), error_msg)
                logger.warning(f"Failed: {queue_item.item_name!r} — {error_msg}")
                failures += 1
                continue
            try:
                self._queue_repo.mark_complete(queue_item.id, _utcnow())
                logger.debug(f"Completed: {queue_item.item_name!r}")
                success += 1
            except Exception as exc:
                # Persistence of the item succeeded; best-effort failure record.
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.error(
                    f"Item {queue_item.item_name!r} saved but mark_complete failed: "
                    f"{error_msg}"
                )
                self._queue_repo.mark_failed(queue_item.id, _utcnow(), error_msg)
                failures += 1

        logger.info(f"Queue cycle complete: {success} success, {failures} failed")
        return success, failures

    # ── Status ───────────────────────────────────────────────────────────────

    def get_status(self) -> SyncStatus:
        """Return a snapshot of current queue and update-page sync state.

        Returns:
            :class:`SyncStatus` with ``queue_stats`` and per-page
            :class:`UpdatePageStatus` keyed by ``page_name``.

        Example:
            >>> status = syncer.get_status()
            >>> status.queue_stats.pending
            0
        """
        stats = self._queue_repo.get_queue_stats()
        pages = {p.page_name: p for p in self._queue_repo.list_update_pages()}
        return SyncStatus(queue_stats=stats, update_pages=pages)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _build_page_url(self, page_name: str) -> str:
        return f"{_PAGE_PREFIX}{page_name}"

    def _refresh_wiki_timestamp(self, page_name: str) -> None:
        """Query the MediaWiki API and store the result in the queue database."""
        try:
            modified_at = self._api_client.get_last_modified(page_name)
            self._queue_repo.set_wiki_modified_at(page_name, modified_at)
        except Exception as exc:
            # Non-fatal: treat the page as potentially stale on API failure.
            logger.warning(
                f"Could not fetch wiki_modified_at for {page_name!r}: {exc}. "
                "Page will be treated as needing re-sync."
            )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
