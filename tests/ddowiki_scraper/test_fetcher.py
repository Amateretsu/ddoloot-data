"""Unit tests for the fetcher module.

Tests WikiFetcher functionality including sync/async fetching, rate limiting,
error handling, robots.txt compliance, and session management using mocked responses.
"""

import time
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
import requests

from ddowiki_scraper.config import WikiFetcherConfig
from ddowiki_scraper.exceptions import (
    FetchError,
    RobotsTxtError,
)
from ddowiki_scraper.fetcher import WikiFetcher


# Fixtures
@pytest.fixture
def default_config() -> WikiFetcherConfig:
    """Create a default WikiFetcherConfig for testing.

    Returns:
        WikiFetcherConfig instance with test-friendly settings
    """
    return WikiFetcherConfig(
        base_url="https://ddowiki.com",
        rate_limit_delay=1.0,  # minimum allowed by config validator
        max_retries=2,
        timeout=5,
        max_concurrent=2,
        respect_robots_txt=False,  # Disable for most tests
    )


@pytest.fixture
def sample_html() -> str:
    """Sample HTML content for mocked responses.

    Returns:
        Minimal valid HTML representing an item page
    """
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Item:Mantle of the Worldshaper</title></head>
    <body>
        <div class="page-header">
            <h1>Mantle of the Worldshaper</h1>
        </div>
        <table class="infobox">
            <tr><th>Minimum Level</th><td>15</td></tr>
            <tr><th>Binding</th><td>Bound to Character on Acquire</td></tr>
        </table>
    </body>
    </html>
    """


@pytest.fixture
def robots_txt() -> str:
    """Sample robots.txt content.

    Returns:
        Valid robots.txt allowing all user agents
    """
    return """
User-agent: *
Disallow: /admin/
Disallow: /private/
Allow: /page/
Crawl-delay: 2
    """


# WikiFetcher Tests
class TestWikiFetcher:
    """Test WikiFetcher initialization and basic functionality."""

    def test_initialization(self, default_config: WikiFetcherConfig) -> None:
        """Test WikiFetcher initializes correctly."""
        fetcher = WikiFetcher(default_config)
        assert fetcher.config == default_config
        assert fetcher._rate_limiter is not None
        assert fetcher._session is None  # Lazy initialization

    @patch("ddowiki_scraper.fetcher.RobotFileParser")
    def test_robots_txt_check_enabled(
        self, mock_parser_class: Mock, default_config: WikiFetcherConfig  # noqa: ARG002
    ) -> None:
        """Test that robots.txt is loaded lazily on the first _can_fetch call."""
        config = WikiFetcherConfig(
            base_url="https://ddowiki.com", respect_robots_txt=True
        )

        mock_parser = Mock()
        mock_parser.can_fetch.return_value = True
        mock_parser_class.return_value = mock_parser

        fetcher = WikiFetcher(config)
        # robots.txt not yet loaded — triggered on first _can_fetch call
        assert not fetcher._robots_loaded
        fetcher._can_fetch("https://ddowiki.com/page/Item:Test")

        mock_parser.set_url.assert_called_once()
        mock_parser.read.assert_called_once()

    def test_robots_txt_check_disabled(self, default_config: WikiFetcherConfig) -> None:
        """Test that robots.txt is not checked when disabled."""
        fetcher = WikiFetcher(default_config)
        assert fetcher._robots_parser is None

    def test_build_item_url(self, default_config: WikiFetcherConfig) -> None:
        """Test URL construction for item pages."""
        fetcher = WikiFetcher(default_config)

        url = fetcher._build_item_url("Sword of Shadow")
        assert url == "https://ddowiki.com/page/Item:Sword_of_Shadow"

        # Test URL encoding
        url = fetcher._build_item_url("Staff of the Seer's Vision")
        assert "Staff_of_the_Seer%27s_Vision" in url

    def test_can_fetch_without_robots(self, default_config: WikiFetcherConfig) -> None:
        """Test that _can_fetch returns True when robots.txt is disabled."""
        fetcher = WikiFetcher(default_config)
        assert fetcher._can_fetch("https://ddowiki.com/page/Item:Test") is True

    @patch("ddowiki_scraper.fetcher.RobotFileParser")
    def test_can_fetch_disallowed_by_robots(self, mock_parser_class: Mock) -> None:
        """Test that RobotsTxtError is raised when URL is disallowed."""
        config = WikiFetcherConfig(
            base_url="https://ddowiki.com", respect_robots_txt=True
        )

        mock_parser = Mock()
        mock_parser.can_fetch.return_value = False
        mock_parser_class.return_value = mock_parser

        fetcher = WikiFetcher(config)

        with pytest.raises(RobotsTxtError):
            fetcher._can_fetch("https://ddowiki.com/admin/secret")


class TestWikiFetcherSync:
    """Test synchronous fetching methods."""

    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_fetch_item_page_success(
        self,
        mock_get: Mock,
        default_config: WikiFetcherConfig,
        sample_html: str,
    ) -> None:
        """Test successful synchronous item page fetch."""
        mock_response = Mock()
        mock_response.text = sample_html
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        fetcher = WikiFetcher(default_config)
        html = fetcher.fetch_item_page("Mantle of the Worldshaper")

        assert html == sample_html
        assert "Mantle of the Worldshaper" in html
        mock_get.assert_called_once()

    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_fetch_item_page_404(
        self, mock_get: Mock, default_config: WikiFetcherConfig
    ) -> None:
        """Test that FetchError is raised for 404 responses."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            response=mock_response
        )
        mock_get.return_value = mock_response

        fetcher = WikiFetcher(default_config)

        with pytest.raises(FetchError, match="not found"):
            fetcher.fetch_item_page("NonexistentItem")

    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_fetch_item_page_timeout(
        self, mock_get: Mock, default_config: WikiFetcherConfig
    ) -> None:
        """Test that FetchError is raised on timeout."""
        mock_get.side_effect = requests.Timeout("Request timed out")

        fetcher = WikiFetcher(default_config)

        with pytest.raises(FetchError):
            fetcher.fetch_item_page("SomeItem")

    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_fetch_url_success(
        self,
        mock_get: Mock,
        default_config: WikiFetcherConfig,
        sample_html: str,
    ) -> None:
        """Test successful arbitrary URL fetch."""
        mock_response = Mock()
        mock_response.text = sample_html
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        fetcher = WikiFetcher(default_config)
        html = fetcher.fetch_url("https://ddowiki.com/page/Category:Items")

        assert html == sample_html

    def test_fetch_url_wrong_domain(self, default_config: WikiFetcherConfig) -> None:
        """Test that ValueError is raised for URLs from wrong domain."""
        fetcher = WikiFetcher(default_config)

        with pytest.raises(ValueError, match="must be from"):
            fetcher.fetch_url("https://example.com/page/Item:Test")

    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_rate_limiting_between_requests(
        self,
        mock_get: Mock,
        sample_html: str,
    ) -> None:
        """Test that rate limiting is enforced between sync requests."""
        config = WikiFetcherConfig(rate_limit_delay=1.0)

        mock_response = Mock()
        mock_response.text = sample_html
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        fetcher = WikiFetcher(config)

        start = time.time()
        fetcher.fetch_item_page("Item1")
        fetcher.fetch_item_page("Item2")
        elapsed = time.time() - start

        # Should have at least one delay
        assert elapsed >= 0.9  # Allow small tolerance


class TestWikiFetcherAsync:
    """Test asynchronous fetching methods."""

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession.get")
    async def test_fetch_item_page_async_success(
        self,
        mock_get: AsyncMock,
        default_config: WikiFetcherConfig,
        sample_html: str,
    ) -> None:
        """Test successful async item page fetch."""
        mock_response = AsyncMock()
        mock_response.text = AsyncMock(return_value=sample_html)
        mock_response.status = 200
        mock_response.raise_for_status = Mock()

        # Setup context manager
        mock_get.return_value.__aenter__.return_value = mock_response

        fetcher = WikiFetcher(default_config)
        html = await fetcher.fetch_item_page_async("Mantle of the Worldshaper")

        assert html == sample_html
        assert "Mantle of the Worldshaper" in html

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession.get")
    async def test_fetch_item_page_async_404(
        self, mock_get: AsyncMock, default_config: WikiFetcherConfig
    ) -> None:
        """Test that FetchError is raised for 404 responses (async)."""
        mock_response = AsyncMock()
        mock_response.status = 404
        # raise_for_status is called synchronously in the code, so use Mock not AsyncMock
        mock_response.raise_for_status = Mock(
            side_effect=aiohttp.ClientResponseError(
                request_info=Mock(), history=[], status=404
            )
        )
        mock_get.return_value.__aenter__.return_value = mock_response

        fetcher = WikiFetcher(default_config)

        with pytest.raises(FetchError, match="not found"):
            await fetcher.fetch_item_page_async("NonexistentItem")

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession.get")
    async def test_fetch_multiple_async_success(
        self,
        mock_get: AsyncMock,
        default_config: WikiFetcherConfig,
        sample_html: str,
    ) -> None:
        """Test successful batch async fetch."""
        mock_response = AsyncMock()
        mock_response.text = AsyncMock(return_value=sample_html)
        mock_response.status = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value.__aenter__.return_value = mock_response

        fetcher = WikiFetcher(default_config)
        items = ["Item1", "Item2", "Item3"]
        results = await fetcher.fetch_multiple_async(items)

        assert len(results) == 3
        assert all(name in results for name in items)
        assert all(html == sample_html for html in results.values())

    @pytest.mark.asyncio
    async def test_fetch_multiple_async_empty_list(
        self, default_config: WikiFetcherConfig
    ) -> None:
        """Test that ValueError is raised for empty item list."""
        fetcher = WikiFetcher(default_config)

        with pytest.raises(ValueError, match="cannot be empty"):
            await fetcher.fetch_multiple_async([])

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession.get")
    async def test_fetch_multiple_async_partial_failure(
        self,
        mock_get: AsyncMock,
        default_config: WikiFetcherConfig,
        sample_html: str,
    ) -> None:
        """Test batch fetch with some failures."""
        call_count = 0

        async def side_effect(*_args: Any, **_kwargs: Any) -> AsyncMock:
            nonlocal call_count
            call_count += 1

            mock_response = AsyncMock()

            if call_count == 2:
                # Second call fails
                # raise_for_status is called synchronously, so use Mock not AsyncMock
                mock_response.raise_for_status = Mock(
                    side_effect=aiohttp.ClientResponseError(
                        request_info=Mock(), history=[], status=500
                    )
                )
            else:
                mock_response.text = AsyncMock(return_value=sample_html)
                mock_response.status = 200
                mock_response.raise_for_status = Mock()

            return mock_response

        mock_get.return_value.__aenter__.side_effect = side_effect

        fetcher = WikiFetcher(default_config)
        results = await fetcher.fetch_multiple_async(["Item1", "Item2", "Item3"])

        # Two should succeed, one should fail
        successful = sum(1 for v in results.values() if v is not None)
        assert successful == 2

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession.get")
    async def test_fetch_url_async_success(
        self,
        mock_get: AsyncMock,
        default_config: WikiFetcherConfig,
        sample_html: str,
    ) -> None:
        """Test successful async arbitrary URL fetch."""
        mock_response = AsyncMock()
        mock_response.text = AsyncMock(return_value=sample_html)
        mock_response.status = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value.__aenter__.return_value = mock_response

        fetcher = WikiFetcher(default_config)
        html = await fetcher.fetch_url_async("https://ddowiki.com/page/Category:Items")

        assert html == sample_html


class TestWikiFetcherContextManagers:
    """Test context manager functionality."""

    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_sync_context_manager(
        self, mock_get: Mock, default_config: WikiFetcherConfig, sample_html: str
    ) -> None:
        """Test sync context manager closes session."""
        mock_response = Mock()
        mock_response.text = sample_html
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with WikiFetcher(default_config) as fetcher:
            html = fetcher.fetch_item_page("Test")
            assert html == sample_html

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession.get")
    async def test_async_context_manager(
        self, mock_get: AsyncMock, default_config: WikiFetcherConfig, sample_html: str
    ) -> None:
        """Test async context manager closes session."""
        mock_response = AsyncMock()
        mock_response.text = AsyncMock(return_value=sample_html)
        mock_response.status = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value.__aenter__.return_value = mock_response

        async with WikiFetcher(default_config) as fetcher:
            html = await fetcher.fetch_item_page_async("Test")
            assert html == sample_html

    @pytest.mark.asyncio
    async def test_close_method(self, default_config: WikiFetcherConfig) -> None:
        """Test that close method can be called multiple times safely."""
        fetcher = WikiFetcher(default_config)

        # Create sessions
        fetcher._get_session()
        await fetcher._get_async_session()

        # Close should work multiple times
        await fetcher.close()
        await fetcher.close()


class TestWikiFetcherRetries:
    """Test retry logic and exponential backoff."""

    @patch("time.sleep")  # Prevent exponential backoff delays during test
    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_retry_on_transient_error(
        self,
        mock_get: Mock,
        _mock_sleep: Mock,  # noqa: PT019
        default_config: WikiFetcherConfig,
        sample_html: str,
    ) -> None:
        """Test that transient errors trigger retries."""
        # First two calls fail, third succeeds
        mock_get.side_effect = [
            requests.ConnectionError("Connection failed"),
            requests.ConnectionError("Connection failed"),
            Mock(text=sample_html, status_code=200),
        ]

        fetcher = WikiFetcher(default_config)
        html = fetcher.fetch_item_page("Test")

        assert html == sample_html
        assert mock_get.call_count == 3

    @patch("time.sleep")  # Prevent exponential backoff delays during test
    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_max_retries_exceeded(
        self,
        mock_get: Mock,
        _mock_sleep: Mock,  # noqa: PT019
        default_config: WikiFetcherConfig,
    ) -> None:
        """Test that FetchError is raised after max retries."""
        mock_get.side_effect = requests.ConnectionError("Connection failed")

        fetcher = WikiFetcher(default_config)

        with pytest.raises(FetchError):
            fetcher.fetch_item_page("Test")

        # stop_after_attempt(3) in the @retry decorator means 3 total attempts
        assert mock_get.call_count == 3


class TestWikiFetcherRobotsErrors:
    """Test robots.txt error handling paths."""

    @patch("ddowiki_scraper.fetcher.RobotFileParser")
    def test_robots_txt_fail_closed_raises_robots_txt_error(
        self, mock_parser_class: Mock
    ) -> None:
        """Test that RobotsTxtError is raised when robots.txt fails and fail_open=False."""
        config = WikiFetcherConfig(
            base_url="https://ddowiki.com",
            respect_robots_txt=True,
            robots_txt_fail_open=False,
        )

        mock_parser = Mock()
        mock_parser.read.side_effect = IOError("network error")
        mock_parser_class.return_value = mock_parser

        fetcher = WikiFetcher(config)

        with pytest.raises(RobotsTxtError):
            fetcher._can_fetch("https://ddowiki.com/page/Item:Sword")

    @patch("ddowiki_scraper.fetcher.RobotFileParser")
    def test_robots_txt_fail_open_returns_true(self, mock_parser_class: Mock) -> None:
        """Test that _can_fetch returns True when robots.txt fails and fail_open=True."""
        config = WikiFetcherConfig(
            base_url="https://ddowiki.com",
            respect_robots_txt=True,
            robots_txt_fail_open=True,
        )

        mock_parser = Mock()
        mock_parser.read.side_effect = IOError("network error")
        mock_parser_class.return_value = mock_parser

        fetcher = WikiFetcher(config)

        result = fetcher._can_fetch("https://ddowiki.com/page/Item:Sword")
        assert result is True


class TestWikiFetcherSyncErrors:
    """Test sync fetch error handling paths."""

    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_fetch_item_page_non_404_http_error(
        self, mock_get: Mock, default_config: WikiFetcherConfig
    ) -> None:
        """Test that FetchError is raised for non-404 HTTP errors (e.g. 500)."""
        mock_response = Mock()
        mock_response.status_code = 500
        http_error = requests.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error
        mock_get.return_value = mock_response

        mock_rate_limiter = Mock()
        mock_rate_limiter.wait_sync = Mock()
        fetcher = WikiFetcher(default_config, rate_limiter=mock_rate_limiter)

        with pytest.raises(FetchError):
            fetcher.fetch_item_page("SomeItem")

    def test_fetch_item_page_unexpected_exception_raises_fetch_error(
        self, default_config: WikiFetcherConfig
    ) -> None:
        """Test that a generic Exception in _do_fetch_sync is wrapped in FetchError."""
        mock_rate_limiter = Mock()
        mock_rate_limiter.wait_sync = Mock()
        fetcher = WikiFetcher(default_config, rate_limiter=mock_rate_limiter)

        with patch.object(
            fetcher, "_do_fetch_sync", side_effect=Exception("unexpected")
        ):
            with pytest.raises(FetchError):
                fetcher.fetch_item_page("SomeItem")

    @patch("ddowiki_scraper.fetcher.requests.Session.get")
    def test_fetch_url_request_exception_raises_fetch_error(
        self, mock_get: Mock, default_config: WikiFetcherConfig
    ) -> None:
        """Test that requests.RequestException in fetch_url is wrapped in FetchError."""
        mock_get.side_effect = requests.ConnectionError("conn refused")

        mock_rate_limiter = Mock()
        mock_rate_limiter.wait_sync = Mock()
        fetcher = WikiFetcher(default_config, rate_limiter=mock_rate_limiter)

        with pytest.raises(FetchError):
            fetcher.fetch_url("https://ddowiki.com/page/Category:Items")


class TestWikiFetcherContextManagerNullSession:
    """Test the sync context manager __exit__ when session is None."""

    def test_exit_with_no_session_does_not_raise(
        self, default_config: WikiFetcherConfig
    ) -> None:
        """Test that __exit__ is a no-op when _session is None (never opened)."""
        # Use context manager without making any requests — _session stays None
        with WikiFetcher(default_config) as fetcher:
            assert fetcher._session is None
        # No assertion needed: reaching here means __exit__ did not raise
