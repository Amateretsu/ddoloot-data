"""Custom exceptions for the item_normalizer package.

Exception hierarchy:
    ItemNormalizerError (base)
    ├── ParseError      — HTML did not match expected MediaWiki structure
    └── NormalizationError — Parsed value could not be coerced to expected type
"""

from typing import Optional


class ItemNormalizerError(Exception):
    """Base exception for all item_normalizer errors."""


class ParseError(ItemNormalizerError):
    """Raised when the HTML cannot be structurally parsed.

    This typically means the fetcher returned a non-item page such as a
    redirect, error page, or search result. The item should be skipped.

    Attributes:
        field: The field that failed to parse (optional)
        raw_value: The raw text that caused the failure (optional)

    Example:
        >>> raise ParseError("No wikitable found", field="infobox")
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        raw_value: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.raw_value = raw_value


class NormalizationError(ItemNormalizerError):
    """Raised when a parsed string cannot be coerced to the expected type.

    Unlike ParseError, this indicates a programming error or a novel page
    format that the normalizer does not handle. Field-level coercion failures
    are logged as warnings and set to None instead of raising this exception;
    NormalizationError is reserved for unrecoverable failures.

    Attributes:
        field: The field name that failed
        raw_value: The raw string value

    Example:
        >>> raise NormalizationError("Pydantic rejected assembled data", field="name")
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        raw_value: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.raw_value = raw_value
