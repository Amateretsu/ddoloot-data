"""item_db — SQLite persistence layer for DDO item data.

Public API:
    ItemRepository  : insert, upsert, query, and delete DDOItem objects
    ItemFilter      : search criteria dataclass for ItemRepository.search()
    ItemDbError     : base exception for all item_db errors
    ItemNotFoundError : raised when a requested item does not exist
    DuplicateItemError : raised when saving a name that already exists
    SchemaError     : raised when the database schema cannot be initialized

Example:
    >>> from item_db import ItemRepository, ItemFilter
    >>> with ItemRepository("loot.db") as repo:
    ...     repo.upsert(item)
    ...     results = repo.search(ItemFilter(slot="Back", minimum_level_max=20))
"""

from item_db._filters import ItemFilter
from item_db.exceptions import (
    DuplicateItemError,
    ItemDbError,
    ItemNotFoundError,
    SchemaError,
)
from item_db.protocols import ItemRepositoryProtocol
from item_db.repository import ItemRepository

__all__ = [
    "DuplicateItemError",
    "ItemDbError",
    "ItemFilter",
    "ItemNotFoundError",
    "ItemRepository",
    "ItemRepositoryProtocol",
    "SchemaError",
]
