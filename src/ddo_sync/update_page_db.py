"""SQLite-backed repository for update-page sync state.

Tracks which DDO Wiki named-item update pages have been registered, when they
were last synced, and what the wiki reports as their last-modified timestamp.
``needs_resync`` is a computed property on :class:`UpdatePageStatus` — no DB
logic required.

Example:
    >>> from ddo_sync.update_page_db import UpdatePageRepository
    >>> with UpdatePageRepository(":memory:") as repo:
    ...     repo.register("Update_5_named_items",
    ...                   "https://ddowiki.com/page/Update_5_named_items")
    ...     pages = repo.list_all()
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional

from loguru import logger

from ddo_sync.exceptions import QueueDbError, QueueSchemaError
from ddo_sync.models import UpdatePageStatus

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS update_pages (
    page_name       TEXT PRIMARY KEY,
    page_url        TEXT NOT NULL,
    last_synced_at  TEXT,
    wiki_modified_at TEXT
);
"""


class UpdatePageRepository:
    """SQLite-backed persistence for update-page sync state.

    Tracks which DDO Wiki named-item update pages are registered, when each
    was last synced, and the MediaWiki-reported last-modified timestamp.
    The ``needs_resync`` decision is delegated to
    :attr:`UpdatePageStatus.needs_resync` — no logic lives here.

    Args:
        db_path: Path to the SQLite file.  Pass ``":memory:"`` for tests.

    Example:
        >>> with UpdatePageRepository("queue.db") as repo:
        ...     repo.register("Update_5_named_items", page_url)
        ...     pages = repo.list_all()
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> UpdatePageRepository:
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
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()
            logger.debug(f"update_page_db opened: {self._db_path!r}")
        except sqlite3.Error as exc:
            self._conn = None
            raise QueueSchemaError(
                f"Failed to initialise update_page schema: {exc}"
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
            logger.debug(f"update_page_db closed: {self._db_path!r}")

    # ── Writes ────────────────────────────────────────────────────────────────

    def register(self, page_name: str, page_url: str) -> None:
        """Insert a new update-page row, or do nothing if it already exists.

        Args:
            page_name: Natural key, e.g. ``"Update_5_named_items"``.
            page_url:  Full URL of the update page.

        Raises:
            QueueDbError: On SQLite error.
        """
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO update_pages (page_name, page_url)"
                    " VALUES (?, ?)",
                    (page_name, page_url),
                )
            logger.debug(f"Registered update page: {page_name!r}")
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to register update page {page_name!r}: {exc}"
            ) from exc

    def mark_synced(self, page_name: str, synced_at: datetime) -> None:
        """Record when item links were last successfully parsed from the page.

        Args:
            page_name: Natural key.
            synced_at: UTC datetime to record.
        """
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE update_pages SET last_synced_at = ? WHERE page_name = ?",
                    (_iso(synced_at), page_name),
                )
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to mark page synced {page_name!r}: {exc}"
            ) from exc

    def set_wiki_modified_at(
        self, page_name: str, modified_at: Optional[datetime]
    ) -> None:
        """Store the ``wiki_modified_at`` timestamp from the MediaWiki API.

        Args:
            page_name:   Natural key.
            modified_at: UTC datetime from the API, or ``None`` if not found.
        """
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE update_pages SET wiki_modified_at = ?"
                    " WHERE page_name = ?",
                    (_iso(modified_at) if modified_at else None, page_name),
                )
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to set wiki_modified_at for {page_name!r}: {exc}"
            ) from exc

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get(self, page_name: str) -> Optional[UpdatePageStatus]:
        """Return the sync state for one tracked page, or ``None``.

        Args:
            page_name: Natural key.
        """
        try:
            row = (
                self._get_conn()
                .execute("SELECT * FROM update_pages WHERE page_name = ?", (page_name,))
                .fetchone()
            )
            return _row_to_status(row) if row else None
        except sqlite3.Error as exc:
            raise QueueDbError(
                f"Failed to get update page status for {page_name!r}: {exc}"
            ) from exc

    def list_all(self) -> List[UpdatePageStatus]:
        """Return :class:`UpdatePageStatus` for every registered update page."""
        try:
            rows = (
                self._get_conn()
                .execute("SELECT * FROM update_pages ORDER BY page_name")
                .fetchall()
            )
            return [_row_to_status(r) for r in rows]
        except sqlite3.Error as exc:
            raise QueueDbError(f"Failed to list update pages: {exc}") from exc

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        return self._conn  # type: ignore[return-value]


# ── Module-level helpers ──────────────────────────────────────────────────────


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_status(row: sqlite3.Row) -> UpdatePageStatus:
    return UpdatePageStatus(
        page_name=row["page_name"],
        page_url=row["page_url"],
        last_synced_at=_parse_iso(row["last_synced_at"]),
        wiki_modified_at=_parse_iso(row["wiki_modified_at"]),
    )
