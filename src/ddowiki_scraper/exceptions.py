"""Custom exception classes for DDO Wiki fetcher.

This module defines a hierarchy of exceptions used throughout the fetcher
module to provide specific, actionable error information for different
failure scenarios.

Exception Hierarchy:
    WikiFetcherError (base)
    ├── RateLimitError - Rate limiting failures
    ├── RobotsTxtError - robots.txt compliance violations
    └── FetchError - HTTP/network request failures

Example:
    >>> from ddowiki_scraper.exceptions import FetchError
    >>> try:
    ...     html = fetcher.fetch_item_page("NonexistentItem")
    ... except FetchError as e:
    ...     print(f"Failed to fetch: {e}")
"""

from __future__ import annotations


class WikiFetcherError(Exception):
    """Base exception for all WikiFetcher errors.

    All custom exceptions in the fetcher module inherit from this base class,
    allowing users to catch all fetcher-related errors with a single except clause.

    This exception should not be raised directly; use one of the specific
    subclasses instead.

    Example:
        >>> try:
        ...     fetcher.fetch_item_page("Item")
        ... except WikiFetcherError as e:
        ...     # Catches all fetcher-related errors
        ...     logger.error(f"Fetcher error: {e}")
    """

    pass


class RateLimitError(WikiFetcherError):
    """Raised when rate limiting fails or is violated.

    This exception indicates a problem with the rate limiting mechanism itself,
    not a normal rate limit delay. Typical causes include:
    - Lock acquisition failure
    - Timer calculation errors
    - Threading/async coordination issues

    Normal rate limiting delays do not raise this exception.

    Attributes:
        message: Description of the rate limiting failure

    Example:
        >>> try:
        ...     await rate_limiter.wait_async()
        ... except RateLimitError as e:
        ...     logger.critical(f"Rate limiter malfunction: {e}")
        ...     # This is unexpected - may indicate a bug
    """

    pass


class RobotsTxtError(WikiFetcherError):
    """Raised when robots.txt disallows scraping the requested URL.

    This exception is raised when:
    1. respect_robots_txt is enabled in config
    2. robots.txt was successfully parsed
    3. The requested URL is explicitly disallowed for our User-Agent

    This is an ethical scraping boundary - do not bypass this exception.

    Attributes:
        message: Description of which URL was disallowed
        url: The URL that was blocked (if provided)

    Example:
        >>> try:
        ...     html = fetcher.fetch_url("https://ddowiki.com/admin/private")
        ... except RobotsTxtError as e:
        ...     logger.warning(f"Blocked by robots.txt: {e}")
        ...     # Respect this - do not retry
    """

    def __init__(self, message: str, url: str | None = None) -> None:
        """Initialize RobotsTxtError with optional URL context.

        Args:
            message: Error message describing the violation
            url: The URL that was disallowed (optional)
        """
        super().__init__(message)
        self.url = url


class FetchError(WikiFetcherError):
    """Raised when fetching HTML fails after all retries.

    This is the most common exception and indicates that an HTTP request
    could not be completed successfully. Common causes include:
    - Network connectivity issues
    - HTTP 404 (page not found)
    - HTTP 500+ (server errors)
    - Request timeouts
    - DNS resolution failures

    The exception message includes context about what failed and why.

    Attributes:
        message: Description of the fetch failure
        status_code: HTTP status code if available (optional)
        url: The URL that failed (optional)

    Example:
        >>> try:
        ...     html = fetcher.fetch_item_page("NonexistentItem")
        ... except FetchError as e:
        ...     if e.status_code == 404:
        ...         logger.info(f"Item not found: {e}")
        ...     else:
        ...         logger.error(f"Fetch failed: {e}")
        ...         # May want to retry with different strategy
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        """Initialize FetchError with optional HTTP context.

        Args:
            message: Error message describing the failure
            status_code: HTTP status code if the request reached the server
            url: The URL that failed to fetch
        """
        super().__init__(message)
        self.status_code = status_code
        self.url = url

    def __str__(self) -> str:
        """Return a detailed string representation of the error.

        Returns:
            Formatted error message with status code and URL if available

        Example:
            >>> e = FetchError("Not found", status_code=404, url="https://example.com/page")
            >>> str(e)
            'Not found (HTTP 404) at https://example.com/page'
        """
        parts = [super().__str__()]

        if self.status_code is not None:
            parts.append(f"(HTTP {self.status_code})")

        if self.url is not None:
            parts.append(f"at {self.url}")

        return " ".join(parts)
