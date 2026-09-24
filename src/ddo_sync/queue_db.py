"""Queue module: the SQLite crawl ledger for update pages and the Named Items they list.

One repository class, :class:`QueueRepository`, owns both tables of one SQLite file:

- ``update_pages``: every tracked ``Update_<N>_named_items`` page, when its item links were
  last read and the revision id of the copy they were read from;
- ``scrape_queue``: one row per item link found on an update page, with its status
  (``pending -> in_progress -> complete | failed | skipped``).

The database is local and disposable: there are no migrations. A file written by an older
schema is refused with :class:`~ddo_sync.exceptions.QueueSchemaError`; delete it and rerun.

Example:
    >>> with QueueRepository("data/queue.db") as qr:
    ...     qr.register_update_page("Update_5_named_items",
    ...                             "https://ddowiki.com/page/Update_5_named_items")
    ...     qr.enqueue_items(links)
    ...     items = qr.get_pending_items(limit=10)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from loguru import logger

from ddo_sync.exceptions import QueueDbError, QueueSchemaError
from ddo_sync.models import ItemLink, QueueItem, QueueStats, UpdatePageStatus

# Stored in PRAGMA user_version. Version 1 had update_pages.wiki_modified_at (from the
# MediaWiki API); version 2 records the revision id read from the page HTML instead.
QUEUE_SCHEMA_VERSION: int = 2

_SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS update_pages (
    page_name      TEXT PRIMARY KEY,  -- e.g. "Update_5_named_items"
    page_url       TEXT NOT NULL,     -- e.g. "https://ddowiki.com/page/Update_5_named_items"
    last_synced_at TEXT,              -- ISO 8601 UTC when item links were last read, or NULL
    revision_id    INTEGER            -- wgCurRevisionId of the copy read, or NULL
);

-- UNIQUE (item_name, update_page): re-reading an update page never re-queues an item.
CREATE TABLE IF NOT EXISTS scrape_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name      TEXT    NOT NULL,
    wiki_url       TEXT    NOT NULL,
    update_page    TEXT    NOT NULL REFERENCES update_pages(page_name) ON DELETE CASCADE,
    status         TEXT    NOT NULL DEFAULT 'pending',
    queued_at      TEXT    NOT NULL,
    started_at     TEXT,
    completed_at   TEXT,
    error_message  TEXT,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (item_name, update_page)
);

CREATE INDEX IF NOT EXISTS idx_sq_status      ON scrape_queue(status);
CREATE INDEX IF NOT EXISTS idx_sq_update_page ON scrape_queue(update_page);
"""


class QueueRepository:
    """SQLite-backed persistence for the scrape queue and update page sync state.

    Args:
        db_path: Path to the SQLite file. Pass ``":memory:"`` for tests.

    Example:
        >>> with QueueRepository("queue.db") as qr:
        ...     qr.register_update_page("Update_5_named_items", page_url)
        ...     qr.enqueue_items(links)
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── Context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> QueueRepository:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open the connection and initialize the schema. Idempotent."""
        if self._conn is not None:
            return
        try:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            _apply_schema(self._conn, self._db_path)
            logger.debug(f"queue_db opened: {self._db_path!r}")
        except sqlite3.Error as exc:
            self._close_quietly()
            raise QueueSchemaError(f"Failed to initialize queue schema: {exc}") from exc
        except QueueSchemaError:
            self._close_quietly()
            raise

    def close(self) -> None:
        """Commit and close the connection. Idempotent."""
        if self._conn is None:
            return
        try:
            self._conn.commit()
        except sqlite3.Error:
            pass
        finally:
            self._conn.close()
            self._conn = None
            logger.debug(f"queue_db closed: {self._db_path!r}")

    # ── Update page management ───────────────────────────────────────────────

    def register_update_page(self, page_name: str, page_url: str) -> None:
        """Insert a new update page row, or do nothing if it already exists.

        Args:
            page_name: Natural key, e.g. ``"Update_5_named_items"``.
            page_url:  Full URL of the update page.

        Raises:
            QueueDbError: On SQLite error.
        """
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO update_pages (page_name, page_url) VALUES (?, ?)",
                    (page_name, page_url),
                )
            logger.debug(f"Registered update page: {page_name!r}")
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to register update page {page_name!r}: {exc}"
            ) from exc

    def mark_page_synced(
        self, page_name: str, synced_at: datetime, revision_id: Optional[int] = None
    ) -> None:
        """Record that the page's item links were read, and from which revision.

        Args:
            page_name:   Natural key.
            synced_at:   UTC datetime to record.
            revision_id: ``wgCurRevisionId`` of the copy that was read, or ``None``.
        """
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE update_pages SET last_synced_at = ?, revision_id = ? "
                    "WHERE page_name = ?",
                    (_iso(synced_at), revision_id, page_name),
                )
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to mark page synced {page_name!r}: {exc}"
            ) from exc

    def get_update_page_status(self, page_name: str) -> Optional[UpdatePageStatus]:
        """Return the sync state for one tracked page, or ``None`` if not registered.

        Args:
            page_name: Natural key.

        Returns:
            :class:`UpdatePageStatus`, or ``None``.
        """
        try:
            row = (
                self._get_conn()
                .execute("SELECT * FROM update_pages WHERE page_name = ?", (page_name,))
                .fetchone()
            )
            return _row_to_update_page_status(row) if row else None
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to get update page status for {page_name!r}: {exc}"
            ) from exc

    def list_update_pages(self) -> List[UpdatePageStatus]:
        """Return :class:`UpdatePageStatus` for every registered update page."""
        try:
            rows = (
                self._get_conn()
                .execute("SELECT * FROM update_pages ORDER BY page_name")
                .fetchall()
            )
            return [_row_to_update_page_status(r) for r in rows]
        except sqlite3.Error as exc:
            raise QueueDbError(f"Failed to list update pages: {exc}") from exc

    # ── Queue writes ─────────────────────────────────────────────────────────

    def enqueue_items(self, links: List[ItemLink]) -> int:
        """Add items to the scrape queue, skipping already-queued duplicates.

        Uses ``INSERT OR IGNORE`` against the ``UNIQUE (item_name, update_page)``
        constraint. Items already in the queue for that update page are silently
        skipped regardless of their current status.

        Args:
            links: :class:`ItemLink` objects to enqueue.

        Returns:
            Number of rows actually inserted.

        Raises:
            QueueDbError: On SQLite error.

        Example:
            >>> inserted = qr.enqueue_items(links)
            >>> inserted
            12
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
        """Transition an item from pending to in_progress."""
        self._update_status(item_id, "in_progress", started_at=_iso(started_at))

    def mark_complete(self, item_id: int, completed_at: datetime) -> None:
        """Transition an item to complete status."""
        self._update_status(item_id, "complete", completed_at=_iso(completed_at))

    def mark_failed(
        self, item_id: int, completed_at: datetime, error_message: str
    ) -> None:
        """Record a failure and increment retry_count. Status becomes ``"failed"``."""
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
        """Set an item's status to skipped. No retry will be attempted."""
        self._update_status(item_id, "skipped")

    def reset_failed_to_pending(self, max_retries: int) -> int:
        """Reset failed items with retry_count < max_retries back to pending.

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

    # ── Queue reads ──────────────────────────────────────────────────────────

    def get_pending_items(self, limit: Optional[int] = None) -> List[QueueItem]:
        """Return pending items ordered by ``queued_at`` ascending (oldest first).

        Args:
            limit: Maximum number of rows to return. ``None`` means all pending.

        Returns:
            List of :class:`QueueItem` in FIFO order.
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
            :class:`QueueStats` with counts for each status.
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
            List of :class:`QueueItem`, ordered by ``queued_at``.
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

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _close_quietly(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None

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


# ── Module-level helpers ─────────────────────────────────────────────────────


def _apply_schema(conn: sqlite3.Connection, db_path: str) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    has_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'update_pages'"
    ).fetchone()[0]
    if has_tables and version != QUEUE_SCHEMA_VERSION:
        raise QueueSchemaError(
            f"{db_path} uses queue schema version {version}, not "
            f"{QUEUE_SCHEMA_VERSION}. The queue is disposable: delete the file and rerun."
        )
    conn.executescript(_SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version = {QUEUE_SCHEMA_VERSION}")
    conn.commit()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _row_to_update_page_status(row: sqlite3.Row) -> UpdatePageStatus:
    return UpdatePageStatus(
        page_name=row["page_name"],
        page_url=row["page_url"],
        last_synced_at=_parse_iso(row["last_synced_at"]),
        revision_id=row["revision_id"],
    )


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
