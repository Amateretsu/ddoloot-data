"""SQLite-backed repository for the scrape queue.

Manages individual item rows in ``scrape_queue``, tracking status transitions
from ``pending`` through ``in_progress`` to ``complete``, ``failed``, or
``skipped``.  The ``update_pages`` table is also created (via
:data:`~ddo_sync.schema.QUEUE_SCHEMA_SQL`) because ``scrape_queue`` carries a
foreign-key reference to it — both tables must live in the same file.

Example:
    >>> from ddo_sync.scrape_queue_db import ScrapeQueueRepository
    >>> from ddo_sync.models import ItemLink
    >>> with ScrapeQueueRepository(":memory:") as repo:
    ...     links = [ItemLink("Sword of Shadow",
    ...                       "https://ddowiki.com/page/Item:Sword_of_Shadow",
    ...                       "Update_5_named_items")]
    ...     repo.enqueue_items(links)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from loguru import logger

from ddo_sync.exceptions import QueueDbError, QueueSchemaError
from ddo_sync.models import ItemLink, QueueItem, QueueStats
from ddo_sync.schema import QUEUE_SCHEMA_SQL


class ScrapeQueueRepository:
    """SQLite-backed persistence for the scrape queue.

    Each instance manages ``pending → in_progress → complete | failed | skipped``
    status transitions for individual item rows.  The ``update_pages`` table is
    created automatically (idempotent) because ``scrape_queue`` references it
    via a foreign key.

    Args:
        db_path: Path to the SQLite file.  Pass ``\":memory:\"`` for tests.

    Example:
        >>> with ScrapeQueueRepository("queue.db") as repo:
        ...     inserted = repo.enqueue_items(links)
        ...     pending = repo.get_pending_items(limit=10)
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> ScrapeQueueRepository:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open the connection and initialise the schema.  Idempotent."""
        if self._conn is not None:
            return
        try:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(QUEUE_SCHEMA_SQL)
            self._conn.commit()
            logger.debug(f"scrape_queue_db opened: {self._db_path!r}")
        except sqlite3.Error as exc:
            self._conn = None
            raise QueueSchemaError(
                f"Failed to initialise scrape_queue schema: {exc}"
            ) from exc

    def close(self) -> None:
        """Commit and close the connection.  Idempotent."""
        if self._conn is None:
            return
        try:
            self._conn.commit()
        except sqlite3.Error:
            pass
        finally:
            self._conn.close()
            self._conn = None
            logger.debug(f"scrape_queue_db closed: {self._db_path!r}")

    # ── Writes ────────────────────────────────────────────────────────────────

    def enqueue_items(self, links: List[ItemLink]) -> int:
        """Add items to the scrape queue, skipping already-queued duplicates.

        Uses ``INSERT OR IGNORE`` against the ``UNIQUE (item_name, update_page)``
        constraint.  Items already in the queue for that update page are silently
        skipped regardless of their current status.

        Args:
            links: :class:`~ddo_sync.models.ItemLink` objects to enqueue.

        Returns:
            Number of rows actually inserted.

        Raises:
            QueueDbError: On SQLite error.
        """
        now = _iso(_utcnow())
        inserted = 0
        try:
            with self._get_conn() as conn:
                for link in links:
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO scrape_queue
                            (item_name, wiki_url, update_page, status, queued_at)
                        VALUES (?, ?, ?, 'pending', ?)
                        """,
                        (link.item_name, link.wiki_url, link.update_page, now),
                    )
                    inserted += cursor.rowcount
            skipped = len(links) - inserted
            logger.debug(
                f"Enqueued {inserted} new items (skipped {skipped} duplicates)"
            )
            return inserted
        except sqlite3.Error as exc:
            raise QueueDbError(f"Failed to enqueue items: {exc}") from exc

    def mark_in_progress(self, item_id: int, started_at: datetime) -> None:
        """Transition an item from ``pending`` to ``in_progress``."""
        self._update_status(item_id, "in_progress", started_at=_iso(started_at))

    def mark_complete(self, item_id: int, completed_at: datetime) -> None:
        """Transition an item to ``complete`` status."""
        self._update_status(item_id, "complete", completed_at=_iso(completed_at))

    def mark_failed(
        self, item_id: int, completed_at: datetime, error_message: str
    ) -> None:
        """Record a failure and increment ``retry_count``.  Status → ``\"failed\"``."""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    UPDATE scrape_queue
                    SET status = 'failed',
                        completed_at = ?,
                        error_message = ?,
                        retry_count = retry_count + 1
                    WHERE id = ?
                    """,
                    (_iso(completed_at), error_message, item_id),
                )
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to mark item {item_id} as failed: {exc}"
            ) from exc

    def mark_skipped(self, item_id: int) -> None:
        """Set an item's status to ``skipped``.  No retry will be attempted."""
        self._update_status(item_id, "skipped")

    def reset_failed_to_pending(self, max_retries: int) -> int:
        """Reset failed items with ``retry_count < max_retries`` back to pending.

        Called at the start of each processing cycle so recoverable failures
        (e.g. transient network errors) are retried automatically.

        Args:
            max_retries: Items with ``retry_count < max_retries`` are reset.

        Returns:
            Number of rows reset.
        """
        try:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    """
                    UPDATE scrape_queue
                    SET status = 'pending', started_at = NULL, completed_at = NULL
                    WHERE status = 'failed' AND retry_count < ?
                    """,
                    (max_retries,),
                )
            count = cursor.rowcount
            if count:
                logger.info(
                    f"Reset {count} failed items to pending (max_retries={max_retries})"
                )
            return count
        except sqlite3.Error as exc:
            raise QueueDbError(f"Failed to reset failed items: {exc}") from exc

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_pending_items(self, limit: Optional[int] = None) -> List[QueueItem]:
        """Return pending items ordered by ``queued_at`` ascending (oldest first).

        Args:
            limit: Maximum number of rows to return.  ``None`` means all pending.

        Returns:
            List of :class:`~ddo_sync.models.QueueItem` in FIFO order.
        """
        sql = "SELECT * FROM scrape_queue WHERE status = 'pending' ORDER BY queued_at"
        params: Tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        try:
            rows = self._get_conn().execute(sql, params).fetchall()
            return [_row_to_queue_item(r) for r in rows]
        except sqlite3.Error as exc:
            raise QueueDbError(f"Failed to get pending items: {exc}") from exc

    def get_queue_stats(self) -> QueueStats:
        """Return aggregate counts by status across the entire queue.

        Returns:
            :class:`~ddo_sync.models.QueueStats` with counts for each status.
        """
        try:
            rows = (
                self._get_conn()
                .execute(
                    "SELECT status, COUNT(*) AS cnt FROM scrape_queue GROUP BY status"
                )
                .fetchall()
            )
            counts = {r["status"]: r["cnt"] for r in rows}
            return QueueStats(
                pending=counts.get("pending", 0),
                in_progress=counts.get("in_progress", 0),
                complete=counts.get("complete", 0),
                failed=counts.get("failed", 0),
                skipped=counts.get("skipped", 0),
            )
        except sqlite3.Error as exc:
            raise QueueDbError(f"Failed to get queue stats: {exc}") from exc

    def get_items_for_update_page(self, page_name: str) -> List[QueueItem]:
        """Return all queue items for a specific update page.

        Args:
            page_name: Natural key of the update page.

        Returns:
            List of :class:`~ddo_sync.models.QueueItem`, ordered by ``queued_at``.
        """
        try:
            rows = (
                self._get_conn()
                .execute(
                    "SELECT * FROM scrape_queue WHERE update_page = ? ORDER BY queued_at",
                    (page_name,),
                )
                .fetchall()
            )
            return [_row_to_queue_item(r) for r in rows]
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to get items for update page {page_name!r}: {exc}"
            ) from exc

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        return self._conn  # type: ignore[return-value]

    def _update_status(
        self,
        item_id: int,
        status: str,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    UPDATE scrape_queue
                    SET status = ?,
                        started_at = COALESCE(?, started_at),
                        completed_at = COALESCE(?, completed_at)
                    WHERE id = ?
                    """,
                    (status, started_at, completed_at, item_id),
                )
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to update status of item {item_id} to {status!r}: {exc}"
            ) from exc


# ── Module-level helpers ──────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _row_to_queue_item(row: sqlite3.Row) -> QueueItem:
    return QueueItem(
        id=row["id"],
        item_name=row["item_name"],
        wiki_url=row["wiki_url"],
        update_page=row["update_page"],
        status=row["status"],
        queued_at=datetime.fromisoformat(row["queued_at"]),
        started_at=_parse_iso(row["started_at"]),
        completed_at=_parse_iso(row["completed_at"]),
        error_message=row["error_message"],
        retry_count=row["retry_count"],
    )
