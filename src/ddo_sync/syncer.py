"""DDOSyncer — orchestrates the full DDO item sync pipeline.

Wires the Page Store (behind :class:`~ddo_sync.protocols.PageStoreProtocol`), the
discovery module (update page -> item links), the queue module
(:class:`~ddo_sync.queue_db.QueueRepository`), the Scraped Item extractor (called
in-process through ``extract()``) and a
:class:`~ddo_sync.protocols.ScrapedItemWriterProtocol` adapter into one sync cycle.

Every page read goes through the Page Store, so a page already held costs no request;
``refresh=True`` refetches every page the run touches. When the Page Store says the run
must stop (:class:`page_store.RunStoppedError`, e.g. a WAF challenge with the browser
fallback disabled) the error propagates and the page is not recorded as failed.

Example:
    >>> from ddo_sync import DDOSyncer, JsonItemWriter
    >>> with PageStore(load_scraper_config()) as store, \\
    ...      QueueRepository("queue.db") as queue_repo:
    ...     writer = JsonItemWriter(Path("cache/extracted"))
    ...     syncer = DDOSyncer(store, writer, queue_repo)
    ...     syncer.register_update_page("Update_5_named_items")
    ...     syncer.sync_all()
    ...     print(syncer.get_status())
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from loguru import logger

from ddo_sync.discovery import read_update_page, update_page_url
from ddo_sync.exceptions import UpdatePageError
from ddo_sync.models import ItemLink, SyncStatus
from ddo_sync.protocols import PageStoreProtocol, ScrapedItemWriterProtocol
from ddo_sync.queue_db import QueueRepository
from item_extractor import Config, extract, load_config
from page_store import RunStoppedError


class DDOSyncer:
    """Orchestrates the full DDO item sync pipeline.

    All dependencies are injected so callers control lifecycle (context
    managers, in-memory testing).

    Args:
        page_store:  Object satisfying :class:`~ddo_sync.protocols.PageStoreProtocol`.
        writer:      Object satisfying
                     :class:`~ddo_sync.protocols.ScrapedItemWriterProtocol`.
        queue_repo:  The crawl queue.
        max_retries: Items that have failed this many times are not reset to
                     pending on the next cycle (default: 3).
        extractor_config: Extractor rules; defaults to ``load_config()``
                     (``catalog/extractor/``).
        refresh:     Refetch every page this syncer reads instead of using the copy
                     the Page Store holds (default: False).

    Example:
        >>> syncer = DDOSyncer(page_store, writer, queue_repo)
        >>> syncer.register_update_page("Update_5_named_items")
        >>> syncer.sync_all()
    """

    def __init__(
        self,
        page_store: PageStoreProtocol,
        writer: ScrapedItemWriterProtocol,
        queue_repo: QueueRepository,
        max_retries: int = 3,
        extractor_config: Optional[Config] = None,
        refresh: bool = False,
    ) -> None:
        self._page_store = page_store
        self._refresh = refresh
        self._writer = writer
        self._queue_repo = queue_repo
        self._extractor_config = extractor_config or load_config()
        self._max_retries = max_retries

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
        self._queue_repo.register_update_page(normalized, update_page_url(normalized))
        logger.info(f"Registered update page: {normalized!r}")

    # ── Sync orchestration ───────────────────────────────────────────────────

    def sync_all(self, limit: Optional[int] = None) -> SyncStatus:
        """Run a full sync cycle for all registered update pages.

        Steps:
          1. Reset failed items below ``max_retries`` back to pending.
          2. Read every registered update page (:meth:`sync_update_page`) and queue any
             item link not queued yet. A held page costs no request, so this is cheap; a
             page that cannot be read is logged and skipped.
          3. Process up to *limit* pending items via :meth:`process_queue`.
          4. Return :meth:`get_status`.

        Returns:
            :class:`SyncStatus` snapshot after all processing completes.

        Raises:
            page_store.RunStoppedError: The Page Store says stop the run.
        """
        reset_count = self._queue_repo.reset_failed_to_pending(self._max_retries)
        if reset_count:
            logger.info(f"Reset {reset_count} failed items to pending")

        for page_status in self._queue_repo.list_update_pages():
            try:
                self.sync_update_page(page_status.page_name)
            except UpdatePageError as exc:
                logger.error(
                    f"Failed to sync update page {page_status.page_name!r}: {exc}"
                )

        self.process_queue(limit=limit)
        return self.get_status()

    def sync_update_page(self, page_name: str) -> List[ItemLink]:
        """Read one update page, enqueue its new item links and record the revision read.

        Registers the page first if it is not tracked yet.

        Does **not** process the queue — call :meth:`process_queue` separately.

        Args:
            page_name: Natural key of the update page to sync (spaces accepted).

        Returns:
            List of :class:`ItemLink` objects found on the page.

        Raises:
            UpdatePageError: If the page cannot be fetched or its HTML is empty.
            page_store.RunStoppedError: The Page Store says stop the run.
        """
        normalized = page_name.replace(" ", "_")
        self._queue_repo.register_update_page(normalized, update_page_url(normalized))
        previous = self._queue_repo.get_update_page_status(normalized)
        page = read_update_page(self._page_store, normalized, refresh=self._refresh)
        inserted = self._queue_repo.enqueue_items(page.links)
        self._queue_repo.mark_page_synced(normalized, _utcnow(), page.revision_id)

        if (
            previous is not None
            and previous.revision_id is not None
            and previous.revision_id != page.revision_id
        ):
            logger.info(
                f"{normalized!r} changed: revision {previous.revision_id} -> "
                f"{page.revision_id}"
            )
        logger.info(
            f"Synced {normalized!r}: {len(page.links)} items found, "
            f"{inserted} newly queued"
        )
        return page.links

    def process_queue(self, limit: Optional[int] = None) -> Tuple[int, int]:
        """Process pending items from the scrape queue.

        For each pending item:
          1. Read the item page through the Page Store.
          2. Mark as ``in_progress``.
          3. Extract a Scraped Item via ``item_extractor.extract``.
          4. Hand item and report to the writer; the report carries the item's
             ``update_page`` so the writer can file it by update.
          5. Mark as ``complete``.

        If any step raises, the item is marked as ``failed`` and the error
        message is stored. Other items continue processing. A
        :class:`page_store.RunStoppedError` is not a page failure: it propagates
        and the item stays ``pending``.

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
            try:
                page = self._page_store.get(queue_item.wiki_url, refresh=self._refresh)
            except RunStoppedError:
                raise
            except Exception as exc:
                self._record_failure(queue_item.item_name, queue_item.id, exc)
                failures += 1
                continue
            self._queue_repo.mark_in_progress(queue_item.id, _utcnow())
            try:
                item, report = extract(
                    page.html, queue_item.wiki_url, self._extractor_config
                )
                report = {"update_page": queue_item.update_page, **report}
                self._writer.write(item, report)
            except Exception as exc:
                self._record_failure(queue_item.item_name, queue_item.id, exc)
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

    def _record_failure(self, item_name: str, item_id: int, exc: Exception) -> None:
        error_msg = f"{type(exc).__name__}: {exc}"
        self._queue_repo.mark_failed(item_id, _utcnow(), error_msg)
        logger.warning(f"Failed: {item_name!r} — {error_msg}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
