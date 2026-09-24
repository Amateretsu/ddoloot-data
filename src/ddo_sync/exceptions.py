"""Custom exceptions for the ddo_sync package.

Exception hierarchy:
    DDOSyncError (base)
    ├── UpdatePageError   — failed to fetch or parse an update page
    ├── WikiApiError      — MediaWiki API call failed or returned unexpected JSON
    └── QueueDbError      — SQLite error in the queue database
        └── QueueSchemaError — schema initialization failed
"""

from __future__ import annotations

from typing import Optional


class DDOSyncError(Exception):
    """Base exception for all ddo_sync errors."""


class UpdatePageError(DDOSyncError):
    """Raised when an update page cannot be fetched or parsed.

    Attributes:
        page_url: The URL of the update page that failed.

    Example:
        >>> raise UpdatePageError("No item links found", page_url="https://ddowiki.com/page/...")
    """

    def __init__(self, message: str, page_url: Optional[str] = None) -> None:
        super().__init__(message)
        self.page_url = page_url


class WikiApiError(DDOSyncError):
    """Raised when the MediaWiki API returns an error or unexpected structure.

    Attributes:
        url: The API URL that was called.

    Example:
        >>> raise WikiApiError("Missing 'query' key", url="https://ddowiki.com/api.php")
    """

    def __init__(self, message: str, url: Optional[str] = None) -> None:
        super().__init__(message)
        self.url = url


class QueueDbError(DDOSyncError):
    """Raised on SQLite errors in the scrape queue database."""


class QueueSchemaError(QueueDbError):
    """Raised when the queue database schema cannot be initialized."""
