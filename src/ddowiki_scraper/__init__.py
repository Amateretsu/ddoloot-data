"""DDO Wiki Item Scraper - Production-grade web scraping for ddowiki.com.

This package provides tools for ethically scraping item data from DDO Wiki,
including HTTP fetching with rate limiting, HTML parsing, data normalization,
and SQLite storage.

Main Components:
    - WikiFetcher: HTTP client for retrieving wiki pages
    - WikiFetcherConfig: Configuration for the fetcher
    - Custom exceptions for error handling
    - RateLimiter for ethical scraping

Example:
    >>> from ddowiki_scraper import WikiFetcher, WikiFetcherConfig
    >>>
    >>> config = WikiFetcherConfig(rate_limit_delay=3.0)
    >>> with WikiFetcher(config) as fetcher:
    ...     html = fetcher.fetch_item_page("Mantle of the Worldshaper")
    ...     print(f"Fetched {len(html)} bytes")

For detailed documentation, see individual module docstrings.
"""

from ddowiki_scraper.config import WikiFetcherConfig
from ddowiki_scraper.exceptions import (
    FetchError,
    RateLimitError,
    RobotsTxtError,
    WikiFetcherError,
)
from ddowiki_scraper.fetcher import WikiFetcher
from ddowiki_scraper.rate_limiter import RateLimiter

__version__ = "0.1.0"
__author__ = "Portfolio Project"
__all__ = [
    "FetchError",
    "RateLimitError",
    "RateLimiter",
    "RobotsTxtError",
    "WikiFetcher",
    "WikiFetcherConfig",
    "WikiFetcherError",
    "__version__",
]
