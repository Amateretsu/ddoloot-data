"""Configuration module for DDO Wiki fetcher.

This module provides the WikiFetcherConfig class for validating and managing
all HTTP client configuration parameters including rate limiting, timeouts,
retry behavior, and ethical scraping settings.

Example:
    >>> from ddowiki_scraper.config import WikiFetcherConfig
    >>> config = WikiFetcherConfig(
    ...     base_url="https://ddowiki.com",
    ...     rate_limit_delay=3.0,
    ...     max_retries=5
    ... )
    >>> print(config.rate_limit_delay)
    3.0
"""

from pydantic import BaseModel, Field, HttpUrl, field_validator


class WikiFetcherConfig(BaseModel):
    """Configuration for WikiFetcher HTTP client.

    Validates and stores all parameters needed for ethical web scraping of
    ddowiki.com. Enforces sensible defaults and bounds on critical parameters
    like rate limiting and timeouts.

    Attributes:
        base_url: Base URL of the DDO Wiki (e.g., https://ddowiki.com).
            Must be a valid HTTP or HTTPS URL.
        user_agent: User-Agent string sent with all HTTP requests.
            Should identify the scraper and purpose.
        rate_limit_delay: Minimum delay between consecutive requests in seconds.
            Must be between 1.0 and 10.0 seconds.
        max_retries: Maximum number of retry attempts for failed requests.
            Must be between 1 and 10.
        timeout: Request timeout in seconds. Must be between 5 and 120 seconds.
        max_concurrent: Maximum number of concurrent async requests for batch operations.
            Must be between 1 and 10.
        respect_robots_txt: Whether to check and respect robots.txt directives.
            When True, fetcher will parse robots.txt and block disallowed URLs.

    Example:
        >>> # Default configuration
        >>> config = WikiFetcherConfig()
        >>> config.rate_limit_delay
        2.5

        >>> # Custom configuration for aggressive scraping
        >>> config = WikiFetcherConfig(
        ...     rate_limit_delay=5.0,
        ...     max_concurrent=2,
        ...     max_retries=5
        ... )

        >>> # Configuration with robots.txt disabled (for testing)
        >>> config = WikiFetcherConfig(respect_robots_txt=False)
    """

    base_url: HttpUrl = Field(
        default="https://ddowiki.com",
        description="Base URL of the DDO Wiki",
    )
    user_agent: str = Field(
        default="DDOWikiItemScraper/1.0 (portfolio project; respectful scraping)",
        description="User-Agent header for requests",
    )
    rate_limit_delay: float = Field(
        default=2.5,
        ge=1.0,
        le=10.0,
        description="Minimum delay between requests (seconds)",
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum retry attempts per request",
    )
    timeout: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Request timeout in seconds",
    )
    max_concurrent: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum concurrent async requests",
    )
    respect_robots_txt: bool = Field(
        default=True,
        description="Check robots.txt before scraping",
    )
    robots_txt_fail_open: bool = Field(
        default=True,
        description=(
            "When True (default), allow scraping if robots.txt cannot be fetched. "
            "When False, block all requests if robots.txt is unavailable."
        ),
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: HttpUrl) -> str:
        """Ensure base_url is a valid HTTP/HTTPS URL and normalize it.

        Validates that the URL uses http or https scheme and removes any
        trailing slashes for consistency.

        Args:
            v: The base_url value to validate (HttpUrl from Pydantic)

        Returns:
            String representation of the validated URL without trailing slash

        Raises:
            ValueError: If URL scheme is not http or https

        Example:
            >>> # Trailing slash is removed
            >>> config = WikiFetcherConfig(base_url="https://ddowiki.com/")
            >>> str(config.base_url)
            'https://ddowiki.com'
        """
        url_str = str(v)
        if not url_str.startswith(("http://", "https://")):
            raise ValueError("base_url must use http or https scheme")
        return url_str.rstrip("/")

    @field_validator("user_agent")
    @classmethod
    def validate_user_agent(cls, v: str) -> str:
        """Ensure user_agent is non-empty.

        Args:
            v: The user_agent value to validate

        Returns:
            The validated user_agent string

        Raises:
            ValueError: If user_agent is empty or whitespace-only

        Example:
            >>> config = WikiFetcherConfig(user_agent="MyBot/1.0")
            >>> config.user_agent
            'MyBot/1.0'
        """
        if not v or not v.strip():
            raise ValueError("user_agent cannot be empty")
        return v.strip()

    def get_request_headers(self) -> dict[str, str]:
        """Generate HTTP headers dictionary for requests.

        Creates a complete set of HTTP headers suitable for making requests
        to the wiki, including User-Agent and standard browser headers.

        Returns:
            Dictionary of HTTP header names to values

        Example:
            >>> config = WikiFetcherConfig()
            >>> headers = config.get_request_headers()
            >>> headers["User-Agent"]
            'DDOWikiItemScraper/1.0 (portfolio project; respectful scraping)'
        """
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    class Config:
        """Pydantic model configuration."""

        frozen = False  # Allow modification after creation
        validate_assignment = True  # Validate on attribute assignment
        extra = "forbid"  # Reject unknown fields
