"""Rate limiting module for ethical web scraping.

This module provides thread-safe rate limiting for both synchronous and
asynchronous contexts, ensuring minimum delays between HTTP requests to
avoid overwhelming target servers.

The rate limiter uses a simple time-based approach with locks for thread
safety in concurrent environments.

Example:
    >>> from ddowiki_scraper.rate_limiter import RateLimiter
    >>> limiter = RateLimiter(delay=2.5)
    >>>
    >>> # Synchronous usage
    >>> limiter.wait_sync()
    >>> make_request()
    >>>
    >>> # Asynchronous usage
    >>> await limiter.wait_async()
    >>> await make_async_request()
"""

import asyncio
import time

from loguru import logger

from ddowiki_scraper.exceptions import RateLimitError


class RateLimiter:
    """Thread-safe rate limiter for HTTP requests.

    Uses a simple time-based approach to enforce minimum delays between
    consecutive requests. Safe for both synchronous and asynchronous contexts,
    with separate wait methods for each.

    The limiter tracks the timestamp of the last request and blocks subsequent
    requests until the required delay has elapsed. Thread safety is guaranteed
    via asyncio.Lock for async contexts.

    Attributes:
        delay: Minimum delay between requests in seconds
        _last_request_time: Timestamp of the last request (UNIX time)
        _lock: Asyncio lock for thread-safe async operations

    Example:
        >>> # Basic usage
        >>> limiter = RateLimiter(delay=2.0)
        >>> limiter.wait_sync()  # First call returns immediately
        >>> limiter.wait_sync()  # Second call waits ~2 seconds

        >>> # Async usage
        >>> async def fetch_many():
        ...     limiter = RateLimiter(delay=2.0)
        ...     for item in items:
        ...         await limiter.wait_async()
        ...         await fetch(item)
    """

    def __init__(self, delay: float) -> None:
        """Initialize the rate limiter.

        Args:
            delay: Minimum delay between requests in seconds.
                Must be a positive number.

        Raises:
            ValueError: If delay is not positive

        Example:
            >>> limiter = RateLimiter(delay=2.5)
            >>> limiter.delay
            2.5
        """
        if delay <= 0:
            raise ValueError("delay must be positive")

        self.delay = delay
        self._last_request_time: float = 0.0
        self._lock = asyncio.Lock()

        logger.debug(f"RateLimiter initialized with {delay}s delay")

    async def wait_async(self) -> None:
        """Wait asynchronously until the next request is allowed.

        Ensures at least `delay` seconds have passed since the last request.
        If insufficient time has elapsed, sleeps for the remaining duration.

        Thread-safe for concurrent async usage via asyncio.Lock. Multiple
        concurrent calls will be serialized, each waiting for their turn plus
        the required delay.

        Raises:
            RateLimitError: If lock acquisition fails (rare, indicates serious issue)

        Example:
            >>> limiter = RateLimiter(delay=2.0)
            >>> await limiter.wait_async()  # Returns immediately (first call)
            >>> start = time.time()
            >>> await limiter.wait_async()  # Waits ~2 seconds
            >>> elapsed = time.time() - start
            >>> assert elapsed >= 2.0
        """
        try:
            async with self._lock:
                current_time = time.time()
                elapsed = current_time - self._last_request_time

                if elapsed < self.delay:
                    wait_time = self.delay - elapsed
                    logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)

                self._last_request_time = time.time()

        except Exception as e:
            logger.error(f"Rate limiter failure (async): {e}")
            raise RateLimitError(f"Failed to acquire rate limit lock: {e}") from e

    def wait_sync(self) -> None:
        """Wait synchronously until the next request is allowed.

        Ensures at least `delay` seconds have passed since the last request.
        If insufficient time has elapsed, blocks for the remaining duration.

        Uses simple time-based blocking without locks (assumes single-threaded
        synchronous usage). For multi-threaded synchronous code, consider using
        threading.Lock or switch to async methods.

        Raises:
            RateLimitError: If timing calculation fails (rare, indicates serious issue)

        Example:
            >>> import time
            >>> limiter = RateLimiter(delay=2.0)
            >>> limiter.wait_sync()  # Returns immediately (first call)
            >>> start = time.time()
            >>> limiter.wait_sync()  # Blocks ~2 seconds
            >>> elapsed = time.time() - start
            >>> assert elapsed >= 2.0
        """
        try:
            current_time = time.time()
            elapsed = current_time - self._last_request_time

            if elapsed < self.delay:
                wait_time = self.delay - elapsed
                logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
                time.sleep(wait_time)

            self._last_request_time = time.time()

        except Exception as e:
            logger.error(f"Rate limiter failure (sync): {e}")
            raise RateLimitError(f"Failed to enforce rate limit: {e}") from e

    def reset(self) -> None:
        """Reset the rate limiter to its initial state.

        Clears the last request timestamp, causing the next wait call to
        return immediately. Useful for testing or when starting a new
        scraping session.

        Example:
            >>> limiter = RateLimiter(delay=2.0)
            >>> limiter.wait_sync()
            >>> limiter.reset()  # Clear history
            >>> limiter.wait_sync()  # Returns immediately
        """
        self._last_request_time = 0.0
        logger.debug("RateLimiter reset")

    def get_time_until_ready(self) -> float:
        """Calculate time until the next request can be made.

        Returns the number of seconds that must elapse before the next request
        is allowed. Returns 0.0 if a request can be made immediately.

        Returns:
            Seconds until next request is allowed (0.0 if ready now)

        Example:
            >>> limiter = RateLimiter(delay=2.0)
            >>> limiter.wait_sync()
            >>> time.sleep(1.0)
            >>> limiter.get_time_until_ready()
            1.0  # Approximately
        """
        current_time = time.time()
        elapsed = current_time - self._last_request_time
        remaining = max(0.0, self.delay - elapsed)

        return remaining

    def is_ready(self) -> bool:
        """Check if a request can be made immediately.

        Returns True if sufficient time has passed since the last request,
        False otherwise. Does not block or modify state.

        Returns:
            True if delay has elapsed and request can proceed, False otherwise

        Example:
            >>> limiter = RateLimiter(delay=2.0)
            >>> limiter.is_ready()
            True  # No previous requests
            >>> limiter.wait_sync()
            >>> limiter.is_ready()
            False  # Just made a request
            >>> time.sleep(2.0)
            >>> limiter.is_ready()
            True  # Delay elapsed
        """
        return self.get_time_until_ready() == 0.0

    def __repr__(self) -> str:
        """Return string representation of the rate limiter.

        Returns:
            String showing delay and ready status

        Example:
            >>> limiter = RateLimiter(delay=2.5)
            >>> repr(limiter)
            'RateLimiter(delay=2.5s, ready=True)'
        """
        return f"RateLimiter(delay={self.delay}s, ready={self.is_ready()})"
