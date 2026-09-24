"""Unit tests for the rate_limiter module.

Tests RateLimiter functionality including sync/async rate limiting,
thread safety, and utility methods.
"""

import asyncio
import time

import pytest

from ddowiki_scraper.rate_limiter import RateLimiter


class TestRateLimiter:
    """Test RateLimiter initialization and basic functionality."""

    def test_initialization(self) -> None:
        """Test that RateLimiter initializes correctly."""
        limiter = RateLimiter(delay=2.5)
        assert limiter.delay == 2.5
        assert limiter._last_request_time == 0.0

    def test_initialization_invalid_delay(self) -> None:
        """Test that invalid delay values are rejected."""
        with pytest.raises(ValueError, match="delay must be positive"):
            RateLimiter(delay=0.0)

        with pytest.raises(ValueError, match="delay must be positive"):
            RateLimiter(delay=-1.0)

    def test_repr(self) -> None:
        """Test string representation of rate limiter."""
        limiter = RateLimiter(delay=2.0)
        repr_str = repr(limiter)
        assert "RateLimiter" in repr_str
        assert "delay=2.0" in repr_str
        assert "ready=" in repr_str


class TestRateLimiterSync:
    """Test synchronous rate limiting functionality."""

    def test_sync_rate_limiting(self) -> None:
        """Test that sync rate limiter enforces delays."""
        limiter = RateLimiter(delay=0.2)

        # First call should return immediately
        start = time.time()
        limiter.wait_sync()
        first_elapsed = time.time() - start
        assert first_elapsed < 0.1  # Should be nearly instant

        # Second call should wait ~0.2 seconds
        start = time.time()
        limiter.wait_sync()
        second_elapsed = time.time() - start
        assert second_elapsed >= 0.18  # Allow small tolerance

    def test_sync_multiple_waits(self) -> None:
        """Test multiple sequential sync waits."""
        limiter = RateLimiter(delay=0.1)

        start = time.time()
        for _ in range(3):
            limiter.wait_sync()
        total_elapsed = time.time() - start

        # Should take at least 0.2 seconds (3 calls, 2 waits)
        assert total_elapsed >= 0.18


class TestRateLimiterAsync:
    """Test asynchronous rate limiting functionality."""

    @pytest.mark.asyncio
    async def test_async_rate_limiting(self) -> None:
        """Test that async rate limiter enforces delays."""
        limiter = RateLimiter(delay=0.2)

        # First call should return immediately
        start = time.time()
        await limiter.wait_async()
        first_elapsed = time.time() - start
        assert first_elapsed < 0.1

        # Second call should wait ~0.2 seconds
        start = time.time()
        await limiter.wait_async()
        second_elapsed = time.time() - start
        assert second_elapsed >= 0.18

    @pytest.mark.asyncio
    async def test_async_multiple_waits(self) -> None:
        """Test multiple sequential async waits."""
        limiter = RateLimiter(delay=0.1)

        start = time.time()
        for _ in range(3):
            await limiter.wait_async()
        total_elapsed = time.time() - start

        # Should take at least 0.2 seconds (3 calls, 2 waits)
        assert total_elapsed >= 0.18

    @pytest.mark.asyncio
    async def test_concurrent_async_rate_limiting(self) -> None:
        """Test that rate limiter is thread-safe for concurrent async calls."""
        limiter = RateLimiter(delay=0.1)

        async def make_request() -> float:
            """Simulate a rate-limited request."""
            await limiter.wait_async()
            return time.time()

        # Execute 3 concurrent "requests"
        start = time.time()
        tasks = [make_request() for _ in range(3)]
        timestamps = await asyncio.gather(*tasks)
        total_time = time.time() - start

        # Should take at least 0.2s (3 requests with 0.1s delay each, minus first)
        assert total_time >= 0.18

        # Timestamps should be ordered (requests serialized)
        assert timestamps[0] < timestamps[1] < timestamps[2]

    @pytest.mark.asyncio
    async def test_concurrent_with_high_load(self) -> None:
        """Test rate limiter under high concurrent load."""
        limiter = RateLimiter(delay=0.05)

        async def make_request(request_id: int) -> tuple[int, float]:
            """Make a rate-limited request."""
            await limiter.wait_async()
            return request_id, time.time()

        # 10 concurrent requests
        start = time.time()
        tasks = [make_request(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start

        # Should take at least 0.45s (10 requests, 9 delays of 0.05s)
        assert total_time >= 0.4

        # All requests should complete
        assert len(results) == 10


class TestRateLimiterUtilityMethods:
    """Test utility methods (reset, is_ready, get_time_until_ready)."""

    def test_reset(self) -> None:
        """Test that reset clears the rate limiter state."""
        limiter = RateLimiter(delay=1.0)

        # Make a request
        limiter.wait_sync()
        assert not limiter.is_ready()

        # Reset
        limiter.reset()
        assert limiter.is_ready()

    def test_is_ready_initial_state(self) -> None:
        """Test that rate limiter starts in ready state."""
        limiter = RateLimiter(delay=1.0)
        assert limiter.is_ready()

    def test_is_ready_after_wait(self) -> None:
        """Test is_ready after making a request."""
        limiter = RateLimiter(delay=0.5)

        limiter.wait_sync()
        assert not limiter.is_ready()

        time.sleep(0.5)
        assert limiter.is_ready()

    def test_get_time_until_ready_initial(self) -> None:
        """Test get_time_until_ready in initial state."""
        limiter = RateLimiter(delay=1.0)
        assert limiter.get_time_until_ready() == 0.0

    def test_get_time_until_ready_after_wait(self) -> None:
        """Test get_time_until_ready after making a request."""
        limiter = RateLimiter(delay=1.0)

        limiter.wait_sync()
        time_until = limiter.get_time_until_ready()

        # Should be approximately 1.0 second
        assert 0.9 <= time_until <= 1.0

        # Wait a bit
        time.sleep(0.5)
        time_until = limiter.get_time_until_ready()

        # Should be approximately 0.5 seconds now
        assert 0.4 <= time_until <= 0.6

    def test_get_time_until_ready_after_elapsed(self) -> None:
        """Test get_time_until_ready after delay has elapsed."""
        limiter = RateLimiter(delay=0.2)

        limiter.wait_sync()
        time.sleep(0.3)  # Wait longer than delay

        time_until = limiter.get_time_until_ready()
        assert time_until == 0.0

    @pytest.mark.asyncio
    async def test_reset_async(self) -> None:
        """Test that reset works with async waits."""
        limiter = RateLimiter(delay=1.0)

        await limiter.wait_async()
        assert not limiter.is_ready()

        limiter.reset()
        assert limiter.is_ready()

        # Next wait should be immediate
        start = time.time()
        await limiter.wait_async()
        elapsed = time.time() - start
        assert elapsed < 0.1


class TestRateLimiterEdgeCases:
    """Test edge cases and error conditions."""

    def test_very_small_delay(self) -> None:
        """Test rate limiter with very small delay."""
        limiter = RateLimiter(delay=0.01)

        start = time.time()
        limiter.wait_sync()
        limiter.wait_sync()
        elapsed = time.time() - start

        assert elapsed >= 0.01

    @pytest.mark.asyncio
    async def test_mixed_sync_async_waits(self) -> None:
        """Test that mixing sync and async waits works correctly."""
        limiter = RateLimiter(delay=0.2)

        # Sync wait
        limiter.wait_sync()
        assert not limiter.is_ready()

        # Async wait should still enforce delay
        start = time.time()
        await limiter.wait_async()
        elapsed = time.time() - start
        assert elapsed >= 0.18

    def test_multiple_resets(self) -> None:
        """Test that multiple resets work correctly."""
        limiter = RateLimiter(delay=1.0)

        limiter.reset()
        limiter.reset()
        limiter.reset()

        assert limiter.is_ready()
        assert limiter.get_time_until_ready() == 0.0
