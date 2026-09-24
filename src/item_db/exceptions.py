"""Custom exceptions for the item_db package.

Exception hierarchy:
    ItemDbError (base)
    ├── SchemaError       — database schema could not be initialized
    ├── ItemNotFoundError — requested item does not exist in the database
    └── DuplicateItemError — item already exists (use upsert to overwrite)
"""


class ItemDbError(Exception):
    """Base exception for all item_db errors."""


class SchemaError(ItemDbError):
    """Raised when the database schema cannot be created or verified."""


class ItemNotFoundError(ItemDbError):
    """Raised when a requested item does not exist in the database.

    Attributes:
        name: The item name that was not found

    Example:
        >>> raise ItemNotFoundError("Sword of Shadow")
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"Item not found: {name!r}")
        self.name = name


class DuplicateItemError(ItemDbError):
    """Raised when saving an item whose name already exists.

    Use ItemRepository.upsert() instead of save() to overwrite.

    Attributes:
        name: The item name that already exists

    Example:
        >>> raise DuplicateItemError("Sword of Shadow")
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"Item already exists: {name!r}. Use upsert() to overwrite.")
        self.name = name
