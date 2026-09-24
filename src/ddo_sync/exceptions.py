"""Custom exceptions for the ddo_sync package.

Exception hierarchy:
    DDOSyncError (base)
    ├── UpdatePageError   — failed to read the named-items index or an update page
    └── QueueDbError      — SQLite error in the queue database
        └── QueueSchemaError — schema initialization failed
"""

from __future__ import annotations

from typing import Optional


class DDOSyncError(Exception):
    """Base exception for all ddo_sync errors."""


class UpdatePageError(DDOSyncError):
    """Raised when the named-items index or an update page cannot be read or parsed.

    Attributes:
        page_url: The URL of the update page that failed.

    Example:
        >>> raise UpdatePageError("No item links found", page_url="https://ddowiki.com/page/...")
    """

    def __init__(self, message: str, page_url: Optional[str] = None) -> None:
        super().__init__(message)
        self.page_url = page_url


class QueueDbError(DDOSyncError):
    """Raised on SQLite errors in the scrape queue database."""


class QueueSchemaError(QueueDbError):
    """Raised when the queue database schema cannot be initialized."""
