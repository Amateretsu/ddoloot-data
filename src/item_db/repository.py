"""ItemRepository — the public interface to the item SQLite database.

All reads and writes go through this class. The connection is opened lazily
on first use (or eagerly via open() / the context manager).

Example:
    >>> from item_db import ItemRepository, ItemFilter
    >>> with ItemRepository("loot.db") as repo:
    ...     item_id = repo.upsert(item)
    ...     cloaks = repo.search(ItemFilter(slot="Back", minimum_level_max=20))
"""

import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

from loguru import logger

from item_db._filters import ItemFilter, _build_where_clause
from item_db._reader import _ItemReader
from item_db._writer import _ItemWriter
from item_db.exceptions import (
    DuplicateItemError,
    ItemDbError,
    ItemNotFoundError,
    SchemaError,
)
from item_db.schema import SCHEMA_SQL
from item_normalizer.models import DDOItem


class ItemRepository:
    """SQLite-backed persistence layer for DDOItem objects.

    Items are stored with an auto-assigned integer ID as the primary key.
    The item name has a UNIQUE constraint and can still be used for lookups,
    but the ID is the stable identifier — safe if a name is ever corrected.

    All write methods operate within an explicit transaction; callers do not
    need to manage transactions themselves.

    Args:
        db_path: Path to the SQLite database file. Pass ":memory:" for an
            in-memory database (useful for testing).

    Example:
        >>> with ItemRepository("loot.db") as repo:
        ...     item_id = repo.save(item)
        ...     fetched = repo.get_by_id(item_id)
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── Context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "ItemRepository":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open the database connection and apply the schema.

        Idempotent: safe to call multiple times; subsequent calls are no-ops.

        Raises:
            SchemaError: If a CREATE TABLE statement fails unexpectedly.
        """
        if self._conn is not None:
            return
        try:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()
            logger.debug(f"item_db opened: {self._db_path!r}")
        except sqlite3.Error as exc:
            self._conn = None
            raise SchemaError(f"Failed to initialize schema: {exc}") from exc

    def close(self) -> None:
        """Close the database connection. Commits any pending work. Idempotent."""
        if self._conn is None:
            return
        try:
            self._conn.commit()
        except sqlite3.Error:
            pass
        finally:
            self._conn.close()
            self._conn = None
            logger.debug(f"item_db closed: {self._db_path!r}")

    # ── Writes ───────────────────────────────────────────────────────────────

    def save(self, item: DDOItem) -> int:
        """Insert a new item. Raises DuplicateItemError if name already exists.

        Args:
            item: The DDOItem to persist.

        Returns:
            The integer ID assigned to the new item.

        Raises:
            TypeError: If item is not a DDOItem instance.
            DuplicateItemError: If an item with this name already exists.
            ItemDbError: If a database error occurs.

        Example:
            >>> item_id = repo.save(item)
            >>> item_id
            1
        """
        self._require_ddo_item(item)
        conn = self._get_conn()
        writer = _ItemWriter(conn)
        try:
            with conn:
                item_id = writer.write(item)
            logger.debug(f"Saved item: {item.name!r} (id={item_id})")
            return item_id
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed: items.name" in str(exc):
                raise DuplicateItemError(item.name) from exc
            raise ItemDbError(f"Integrity error saving {item.name!r}: {exc}") from exc
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error saving {item.name!r}: {exc}") from exc

    def upsert(self, item: DDOItem) -> int:
        """Insert or fully replace an existing item.

        If an item with the same name already exists, all of its data is
        deleted via FK cascade before the new data is inserted. A new integer
        ID is assigned on each upsert.

        Args:
            item: The DDOItem to persist or overwrite.

        Returns:
            The integer ID assigned to the item row.

        Raises:
            TypeError: If item is not a DDOItem instance.
            ItemDbError: If a database error occurs.

        Example:
            >>> item_id = repo.upsert(scraped_item)
        """
        self._require_ddo_item(item)
        conn = self._get_conn()
        writer = _ItemWriter(conn)
        try:
            with conn:
                writer.delete_by_name(item.name)
                item_id = writer.write(item)
            logger.debug(f"Upserted item: {item.name!r} (id={item_id})")
            return item_id
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error upserting {item.name!r}: {exc}") from exc

    def delete(self, name: str) -> None:
        """Delete an item by name and cascade all related rows.

        Args:
            name: The item name.

        Raises:
            ItemNotFoundError: If no item with this name exists.
            ItemDbError: If a database error occurs.
        """
        conn = self._get_conn()
        writer = _ItemWriter(conn)
        try:
            with conn:
                deleted = writer.delete_by_name(name)
            if not deleted:
                raise ItemNotFoundError(name)
            logger.debug(f"Deleted item: {name!r}")
        except ItemNotFoundError:
            raise
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error deleting {name!r}: {exc}") from exc

    def delete_by_id(self, item_id: int) -> None:
        """Delete an item by its integer ID and cascade all related rows.

        Args:
            item_id: The integer primary key.

        Raises:
            ItemNotFoundError: If no item with this ID exists.
            ItemDbError: If a database error occurs.
        """
        conn = self._get_conn()
        writer = _ItemWriter(conn)
        try:
            with conn:
                deleted = writer.delete_by_id(item_id)
            if not deleted:
                raise ItemNotFoundError(str(item_id))
            logger.debug(f"Deleted item id={item_id}")
        except ItemNotFoundError:
            raise
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error deleting id={item_id}: {exc}") from exc

    def save_many(
        self, items: List[DDOItem]
    ) -> Tuple[int, List[Tuple[DDOItem, Exception]]]:
        """Bulk upsert a list of DDOItem objects.

        Calls upsert() per item and collects failures without aborting the batch.

        Args:
            items: List of DDOItem objects to persist.

        Returns:
            Tuple of ``(success_count, failures)`` where *failures* is a list
            of ``(item, exception)`` pairs for every item that could not be
            persisted.  Callers can inspect or re-raise individual errors.

        Example:
            >>> saved, failures = repo.save_many(scraped_items)
            >>> for item, exc in failures:
            ...     logger.error(f"Failed to save {item.name!r}: {exc}")
        """
        success = 0
        failures: List[Tuple[DDOItem, Exception]] = []
        for item in items:
            try:
                self.upsert(item)
                success += 1
            except Exception as exc:
                name = getattr(item, "name", repr(item))
                logger.warning(f"save_many: failed to upsert {name!r}: {exc}")
                failures.append((item, exc))
        logger.info(f"save_many complete: {success} saved, {len(failures)} errors")
        return success, failures

    # ── Reads ────────────────────────────────────────────────────────────────

    def get(self, name: str) -> DDOItem:
        """Retrieve one item by exact name.

        Args:
            name: The item name.

        Returns:
            Reconstructed DDOItem.

        Raises:
            ItemNotFoundError: If no item with this name exists.
        """
        item = self.get_or_none(name)
        if item is None:
            raise ItemNotFoundError(name)
        return item

    def get_or_none(self, name: str) -> Optional[DDOItem]:
        """Retrieve one item by exact name, or None if not found."""
        try:
            return _ItemReader(self._get_conn()).read_by_name(name)
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error reading {name!r}: {exc}") from exc

    def get_by_id(self, item_id: int) -> DDOItem:
        """Retrieve one item by its integer ID.

        Args:
            item_id: The integer primary key returned by save() or upsert().

        Returns:
            Reconstructed DDOItem.

        Raises:
            ItemNotFoundError: If no item with this ID exists.

        Example:
            >>> item_id = repo.save(item)
            >>> fetched = repo.get_by_id(item_id)
        """
        item = self.get_by_id_or_none(item_id)
        if item is None:
            raise ItemNotFoundError(str(item_id))
        return item

    def get_by_id_or_none(self, item_id: int) -> Optional[DDOItem]:
        """Retrieve one item by integer ID, or None if not found."""
        try:
            return _ItemReader(self._get_conn()).read_by_id(item_id)
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error reading id={item_id}: {exc}") from exc

    def get_id(self, name: str) -> Optional[int]:
        """Return the integer ID for a given item name, or None if not found.

        Useful for bridging name-based and ID-based lookups without loading
        the full item.

        Args:
            name: The item name.

        Returns:
            Integer ID or None.
        """
        try:
            row = (
                self._get_conn()
                .execute("SELECT id FROM items WHERE name = ?", (name,))
                .fetchone()
            )
            return row["id"] if row else None
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error getting id for {name!r}: {exc}") from exc

    def list_names(self) -> List[str]:
        """Return a sorted list of all item names in the database."""
        try:
            rows = (
                self._get_conn()
                .execute("SELECT name FROM items ORDER BY name")
                .fetchall()
            )
            return [r["name"] for r in rows]
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error listing names: {exc}") from exc

    def count(self) -> int:
        """Return the total number of items in the database."""
        try:
            row = self._get_conn().execute("SELECT COUNT(*) FROM items").fetchone()
            return row[0]
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error counting items: {exc}") from exc

    def exists(self, name: str) -> bool:
        """Return True if an item with this name exists."""
        try:
            row = (
                self._get_conn()
                .execute("SELECT 1 FROM items WHERE name = ?", (name,))
                .fetchone()
            )
            return row is not None
        except sqlite3.Error as exc:
            raise ItemDbError(
                f"Database error checking existence of {name!r}: {exc}"
            ) from exc

    def search(self, filters: ItemFilter) -> List[DDOItem]:
        """Return items matching all provided filter criteria, sorted by name.

        Args:
            filters: An ItemFilter dataclass describing the query constraints.

        Returns:
            List of matching DDOItem objects.
        """
        try:
            sql, params = _build_where_clause(filters)
            rows = self._get_conn().execute(sql, params).fetchall()
            reader = _ItemReader(self._get_conn())
            return [
                item for r in rows if (item := reader.read_by_id(r["id"])) is not None
            ]
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error during search: {exc}") from exc

    def find_by_enchantment(self, enchantment_name: str) -> List[str]:
        """Return names of items that have a matching enchantment (substring match)."""
        try:
            rows = (
                self._get_conn()
                .execute(
                    """
                SELECT DISTINCT i.name FROM items i
                JOIN enchantments e ON e.item_id = i.id
                WHERE e.name LIKE ?
                ORDER BY i.name
                """,
                    (f"%{enchantment_name}%",),
                )
                .fetchall()
            )
            return [r["name"] for r in rows]
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error searching enchantments: {exc}") from exc

    def find_by_set(self, set_name: str) -> List[DDOItem]:
        """Return all items that belong to the named set, sorted by name."""
        try:
            rows = (
                self._get_conn()
                .execute(
                    """
                SELECT i.id FROM items i
                JOIN item_named_set ins ON ins.item_id = i.id
                JOIN named_sets ns ON ns.id = ins.named_set_id
                WHERE ns.name = ?
                ORDER BY i.name
                """,
                    (set_name,),
                )
                .fetchall()
            )
            reader = _ItemReader(self._get_conn())
            return [
                item for r in rows if (item := reader.read_by_id(r["id"])) is not None
            ]
        except sqlite3.Error as exc:
            raise ItemDbError(
                f"Database error searching set {set_name!r}: {exc}"
            ) from exc

    def find_by_quest(self, quest_name: str) -> List[str]:
        """Return names of items that drop in the given quest (substring match)."""
        try:
            rows = (
                self._get_conn()
                .execute(
                    """
                SELECT DISTINCT i.name FROM items i
                JOIN source_quests sq ON sq.item_id = i.id
                WHERE sq.quest_name LIKE ?
                ORDER BY i.name
                """,
                    (f"%{quest_name}%",),
                )
                .fetchall()
            )
            return [r["name"] for r in rows]
        except sqlite3.Error as exc:
            raise ItemDbError(f"Database error searching quests: {exc}") from exc

    def get_scraped_at(self, name: str) -> Optional[datetime]:
        """Return the scraped_at timestamp for an item without loading the full object."""
        try:
            row = (
                self._get_conn()
                .execute("SELECT scraped_at FROM items WHERE name = ?", (name,))
                .fetchone()
            )
            if row is None:
                return None
            return datetime.fromisoformat(row["scraped_at"])
        except sqlite3.Error as exc:
            raise ItemDbError(
                f"Database error reading scraped_at for {name!r}: {exc}"
            ) from exc

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        return self._conn  # type: ignore[return-value]

    @staticmethod
    def _require_ddo_item(item: object) -> None:
        if not isinstance(item, DDOItem):
            raise TypeError(f"Expected DDOItem, got {type(item).__name__}")
