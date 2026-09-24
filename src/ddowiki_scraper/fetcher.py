"""HTTP fetcher for DDO Wiki item pages.

This module provides the WikiFetcher class, a production-grade HTTP client for
retrieving raw HTML from ddowiki.com. It integrates configuration, rate limiting,
and error handling into a cohesive interface for ethical web scraping.

The fetcher supports both synchronous and asynchronous operations, with automatic
retry logic, robots.txt compliance, and concurrent batch fetching capabilities.

Example:
    >>> from ddowiki_scraper import WikiFetcher, WikiFetcherConfig
    >>>
    >>> # Synchronous usage
    >>> config = WikiFetcherConfig(rate_limit_delay=3.0)
    >>> with WikiFetcher(config) as fetcher:
    ...     html = fetcher.fetch_item_page("Mantle of the Worldshaper")
    ...     print(f"Fetched {len(html)} bytes")
    >>>
    >>> # Asynchronous batch usage
    >>> async def fetch_many():
    ...     async with WikiFetcher(config) as fetcher:
    ...         items = ["Sword of Shadow", "Epic Elyd Edge"]
    ...         results = await fetcher.fetch_multiple_async(items)
    ...         return results
"""

import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import aiohttp
import requests
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ddowiki_scraper.config import WikiFetcherConfig
from ddowiki_scraper.exceptions import FetchError, RobotsTxtError
from ddowiki_scraper.rate_limiter import RateLimiter


class WikiFetcher:
    """HTTP client for fetching DDO Wiki item pages.

    Provides both synchronous and asynchronous methods for retrieving HTML
    from ddowiki.com with automatic rate limiting, retry logic, and
    comprehensive error handling.

    The fetcher respects ethical scraping practices including robots.txt
    compliance, rate limiting, proper User-Agent identification, and
    exponential backoff on failures.

    Attributes:
        config: WikiFetcherConfig instance with scraping parameters
        _rate_limiter: RateLimiter instance for enforcing delays
        _robots_parser: RobotFileParser for robots.txt compliance (optional)
        _session: Requests Session for synchronous requests (lazy-initialized)
        _async_session: aiohttp ClientSession for async requests (lazy-initialized)

    Example:
        >>> # Basic synchronous usage
        >>> config = WikiFetcherConfig()
        >>> fetcher = WikiFetcher(config)
        >>> html = fetcher.fetch_item_page("Mantle of the Worldshaper")
        >>>
        >>> # Async with context manager
        >>> async with WikiFetcher(config) as fetcher:
        ...     html = await fetcher.fetch_item_page_async("Sword of Shadow")
    """

    def __init__(
        self,
        config: WikiFetcherConfig,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        """Initialize the WikiFetcher.

        Args:
            config:       Configuration object with scraping parameters.
            rate_limiter: Optional pre-configured :class:`RateLimiter`.  When
                          omitted a new one is created from ``config.rate_limit_delay``.
                          Inject a custom instance in tests to avoid real delays.

        Example:
            >>> config = WikiFetcherConfig(rate_limit_delay=3.0)
            >>> fetcher = WikiFetcher(config)
        """
        self.config = config
        self._rate_limiter = rate_limiter or RateLimiter(config.rate_limit_delay)
        self._robots_parser: Optional[RobotFileParser] = None
        self._robots_loaded: bool = False
        self._session: Optional[requests.Session] = None
        self._async_session: Optional[aiohttp.ClientSession] = None

        logger.info(
            f"WikiFetcher initialized: base_url={config.base_url}, "
            f"rate_limit={config.rate_limit_delay}s"
        )

    def _ensure_robots_loaded(self) -> None:
        """Lazily fetch and parse robots.txt on the first call.  Idempotent.

        Behaviour on failure depends on :attr:`WikiFetcherConfig.robots_txt_fail_open`:

        * ``True``  (default) — log a warning and allow scraping.
        * ``False`` — log an error and raise :exc:`RobotsTxtError`.

        Raises:
            RobotsTxtError: If robots.txt cannot be fetched *and*
                ``robots_txt_fail_open`` is ``False``.
        """
        if self._robots_loaded:
            return
        self._robots_loaded = True  # set before fetch to avoid retry loops

        robots_url = urljoin(str(self.config.base_url), "/robots.txt")
        logger.debug(f"Fetching robots.txt from {robots_url}")

        try:
            self._robots_parser = RobotFileParser()
            self._robots_parser.set_url(robots_url)
            self._robots_parser.read()
            logger.info("Successfully parsed robots.txt")
        except Exception as exc:
            self._robots_parser = None
            if self.config.robots_txt_fail_open:
                logger.warning(
                    f"Failed to fetch robots.txt: {exc}. "
                    "Proceeding (robots_txt_fail_open=True)."
                )
            else:
                logger.error(
                    f"Failed to fetch robots.txt: {exc}. "
                    "Blocking all requests (robots_txt_fail_open=False)."
                )
                raise RobotsTxtError(
                    f"robots.txt unavailable and fail_open=False: {exc}",
                    url=robots_url,
                ) from exc

    def _can_fetch(self, url: str) -> bool:
        """Check if URL can be fetched according to robots.txt.

        Lazily loads robots.txt on first call when ``respect_robots_txt`` is
        enabled.

        Args:
            url: The URL to check.

        Returns:
            ``True`` if fetching is allowed.

        Raises:
            RobotsTxtError: If robots.txt disallows *url*, or if robots.txt is
                unavailable and ``robots_txt_fail_open=False``.

        Example:
            >>> fetcher._can_fetch("https://ddowiki.com/page/Item:Sword")
            True
        """
        if not self.config.respect_robots_txt:
            return True

        self._ensure_robots_loaded()

        if self._robots_parser is None:
            # Load failed but fail_open=True — allow
            return True

        user_agent = self.config.user_agent
        if not self._robots_parser.can_fetch(user_agent, url):
            logger.error(f"robots.txt disallows fetching: {url}")
            raise RobotsTxtError(f"Fetching {url} is disallowed by robots.txt", url=url)

        return True

    def _build_item_url(self, item_name: str) -> str:
        """Construct the full URL for an item page.

        Args:
            item_name: Name of the item (will be URL-encoded)

        Returns:
            Full URL to the item page

        Example:
            >>> fetcher._build_item_url("Sword of Shadow")
            'https://ddowiki.com/page/Item:Sword_of_Shadow'
            >>> fetcher._build_item_url("Staff of the Seer's Vision")
            'https://ddowiki.com/page/Item:Staff_of_the_Seer%27s_Vision'
        """
        # DDO Wiki uses underscores in URLs
        encoded_name = item_name.replace(" ", "_")
        # URL-encode special characters
        encoded_name = quote(encoded_name, safe="_")
        return f"{self.config.base_url}/page/Item:{encoded_name}"

    def _get_session(self) -> requests.Session:
        """Get or create a requests Session with proper headers.

        Lazy-initializes the session on first use. Reuses the same session
        for subsequent requests to benefit from connection pooling.

        Returns:
            Configured requests.Session instance

        Raises:
            FetchError: If session creation fails
        """
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self.config.get_request_headers())
            logger.debug("Created new requests.Session")
        return self._session

    async def _get_async_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp ClientSession with proper headers.

        Lazy-initializes the session on first use. Reuses the same session
        for subsequent requests to benefit from connection pooling.

        Returns:
            Configured aiohttp.ClientSession instance

        Raises:
            FetchError: If session creation fails
        """
        if self._async_session is None or self._async_session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._async_session = aiohttp.ClientSession(
                headers=self.config.get_request_headers(),
                timeout=timeout,
            )
            logger.debug("Created new aiohttp.ClientSession")
        return self._async_session

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _do_fetch_sync(self, url: str, item_name: str) -> str:
        """Inner HTTP fetch with retry support for transient errors.

        Converts HTTP errors (404, 5xx) to FetchError immediately — these are
        not retried. Lets connection/timeout errors propagate as
        requests.RequestException so the retry decorator can handle them.
        """
        session = self._get_session()
        response = session.get(url, timeout=self.config.timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                logger.error(f"Item page not found: {item_name}")
                raise FetchError(
                    f"Item '{item_name}' not found (404)",
                    status_code=404,
                    url=url,
                ) from e
            logger.error(f"HTTP error fetching {item_name}: {e}")
            raise FetchError(
                f"HTTP error: {e}",
                status_code=e.response.status_code,
                url=url,
            ) from e
        html = response.text
        logger.success(f"Successfully fetched {item_name} ({len(html)} bytes)")
        return html

    def fetch_item_page(self, item_name: str) -> str:
        """Fetch HTML for a single item page synchronously.

        Applies rate limiting, checks robots.txt, and retries on transient
        failures with exponential backoff.

        Args:
            item_name: Name of the item to fetch

        Returns:
            Raw HTML content of the item page

        Raises:
            RobotsTxtError: If robots.txt disallows the URL
            FetchError: If fetching fails after all retries (includes 404s)

        Example:
            >>> html = fetcher.fetch_item_page("Mantle of the Worldshaper")
            >>> "Mantle of the Worldshaper" in html
            True
        """
        url = self._build_item_url(item_name)
        logger.info(f"Fetching item page: {item_name} -> {url}")

        self._can_fetch(url)
        self._rate_limiter.wait_sync()

        try:
            return self._do_fetch_sync(url, item_name)
        except FetchError:
            raise
        except requests.RequestException as e:
            logger.error(f"Request failed for {item_name}: {e}")
            raise FetchError(f"Request failed: {e}", url=url) from e
        except Exception as e:
            logger.error(f"Unexpected error fetching {item_name}: {e}")
            raise FetchError(f"Unexpected error: {e}", url=url) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError,)),
        reraise=True,
    )
    async def fetch_item_page_async(self, item_name: str) -> str:
        """Fetch HTML for a single item page asynchronously.

        Applies rate limiting, checks robots.txt, and retries on transient
        failures with exponential backoff.

        Args:
            item_name: Name of the item to fetch

        Returns:
            Raw HTML content of the item page

        Raises:
            RobotsTxtError: If robots.txt disallows the URL
            FetchError: If fetching fails after all retries (includes 404s)

        Example:
            >>> html = await fetcher.fetch_item_page_async("Sword of Shadow")
            >>> "Sword of Shadow" in html
            True
        """
        url = self._build_item_url(item_name)
        logger.info(f"Fetching item page (async): {item_name} -> {url}")

        # Check robots.txt
        self._can_fetch(url)

        # Apply rate limiting
        await self._rate_limiter.wait_async()

        try:
            session = await self._get_async_session()
            async with session.get(url) as response:
                response.raise_for_status()
                html = await response.text()

            logger.success(f"Successfully fetched {item_name} ({len(html)} bytes)")
            return html

        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                logger.error(f"Item page not found: {item_name}")
                raise FetchError(
                    f"Item '{item_name}' not found (404)",
                    status_code=404,
                    url=url,
                ) from e
            logger.error(f"HTTP error fetching {item_name}: {e}")
            raise FetchError(
                f"HTTP error: {e}",
                status_code=e.status,
                url=url,
            ) from e

        except aiohttp.ClientError as e:
            logger.error(f"Request failed for {item_name}: {e}")
            raise FetchError(f"Request failed: {e}", url=url) from e

        except Exception as e:
            logger.error(f"Unexpected error fetching {item_name}: {e}")
            raise FetchError(f"Unexpected error: {e}", url=url) from e

    async def fetch_multiple_async(
        self, item_names: List[str]
    ) -> Dict[str, Optional[str]]:
        """Fetch multiple item pages concurrently with semaphore control.

        Uses asyncio.Semaphore to limit concurrent requests while maintaining
        rate limiting. Failed requests return None instead of raising exceptions,
        allowing partial results.

        Args:
            item_names: List of item names to fetch

        Returns:
            Dictionary mapping item names to their HTML content (or None if failed)

        Raises:
            ValueError: If item_names is empty

        Example:
            >>> items = ["Sword of Shadow", "Mantle of the Worldshaper"]
            >>> results = await fetcher.fetch_multiple_async(items)
            >>> len(results)
            2
            >>> all(html is not None for html in results.values())
            True
        """
        if not item_names:
            raise ValueError("item_names cannot be empty")

        logger.info(f"Fetching {len(item_names)} items concurrently")

        # Semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(self.config.max_concurrent)

        async def fetch_with_semaphore(item_name: str) -> tuple[str, Optional[str]]:
            """Fetch a single item with semaphore control."""
            async with semaphore:
                try:
                    html = await self.fetch_item_page_async(item_name)
                    return item_name, html
                except Exception as e:
                    logger.error(f"Failed to fetch {item_name}: {e}")
                    return item_name, None

        # Execute all fetches concurrently
        tasks = [fetch_with_semaphore(name) for name in item_names]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Convert to dictionary
        result_dict = dict(results)
        successful = sum(1 for v in result_dict.values() if v is not None)
        logger.info(f"Batch fetch complete: {successful}/{len(item_names)} successful")

        return result_dict

    def fetch_url(self, url: str) -> str:
        """Fetch HTML from an arbitrary URL (for category pages, update lists, etc.).

        Args:
            url: Full URL to fetch (must be on the same domain as base_url)

        Returns:
            Raw HTML content

        Raises:
            ValueError: If URL is not from the configured base domain
            RobotsTxtError: If robots.txt disallows the URL
            FetchError: If fetching fails after all retries

        Example:
            >>> html = fetcher.fetch_url("https://ddowiki.com/page/Category:Items")
            >>> "Category:Items" in html
            True
        """
        # Validate URL is from the same domain
        parsed_url = urlparse(url)
        parsed_base = urlparse(str(self.config.base_url))

        if parsed_url.netloc != parsed_base.netloc:
            raise ValueError(
                f"URL must be from {parsed_base.netloc}, got {parsed_url.netloc}"
            )

        logger.info(f"Fetching URL: {url}")

        # Check robots.txt
        self._can_fetch(url)

        # Apply rate limiting
        self._rate_limiter.wait_sync()

        try:
            session = self._get_session()
            response = session.get(url, timeout=self.config.timeout)
            response.raise_for_status()

            html = response.text
            logger.success(f"Successfully fetched URL ({len(html)} bytes)")
            return html

        except requests.RequestException as e:
            logger.error(f"Request failed for {url}: {e}")
            raise FetchError(f"Request failed: {e}", url=url) from e

    async def fetch_url_async(self, url: str) -> str:
        """Fetch HTML from an arbitrary URL asynchronously.

        Args:
            url: Full URL to fetch (must be on the same domain as base_url)

        Returns:
            Raw HTML content

        Raises:
            ValueError: If URL is not from the configured base domain
            RobotsTxtError: If robots.txt disallows the URL
            FetchError: If fetching fails after all retries

        Example:
            >>> html = await fetcher.fetch_url_async("https://ddowiki.com/page/Category:Items")
            >>> "Category:Items" in html
            True
        """
        # Validate URL is from the same domain
        parsed_url = urlparse(url)
        parsed_base = urlparse(str(self.config.base_url))

        if parsed_url.netloc != parsed_base.netloc:
            raise ValueError(
                f"URL must be from {parsed_base.netloc}, got {parsed_url.netloc}"
            )

        logger.info(f"Fetching URL (async): {url}")

        # Check robots.txt
        self._can_fetch(url)

        # Apply rate limiting
        await self._rate_limiter.wait_async()

        try:
            session = await self._get_async_session()
            async with session.get(url) as response:
                response.raise_for_status()
                html = await response.text()

            logger.success(f"Successfully fetched URL ({len(html)} bytes)")
            return html

        except aiohttp.ClientError as e:
            logger.error(f"Request failed for {url}: {e}")
            raise FetchError(f"Request failed: {e}", url=url) from e

    async def close(self) -> None:
        """Close all open HTTP sessions.

        Should be called when done with the fetcher to clean up resources.
        Safe to call multiple times.

        Example:
            >>> await fetcher.close()
        """
        if self._session is not None:
            self._session.close()
            self._session = None
            logger.debug("Closed requests.Session")

        if self._async_session is not None and not self._async_session.closed:
            await self._async_session.close()
            self._async_session = None
            logger.debug("Closed aiohttp.ClientSession")

    def __enter__(self) -> "WikiFetcher":
        """Context manager entry for sync usage.

        Returns:
            Self for context manager protocol

        Example:
            >>> with WikiFetcher(config) as fetcher:
            ...     html = fetcher.fetch_item_page("Sword")
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit for sync usage."""
        if self._session is not None:
            self._session.close()

    async def __aenter__(self) -> "WikiFetcher":
        """Async context manager entry.

        Returns:
            Self for async context manager protocol

        Example:
            >>> async with WikiFetcher(config) as fetcher:
            ...     html = await fetcher.fetch_item_page_async("Sword")
        """
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
