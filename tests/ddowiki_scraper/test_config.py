"""Unit tests for the config module.

Tests WikiFetcherConfig validation, defaults, and helper methods.
"""

import pytest
from pydantic import ValidationError

from ddowiki_scraper.config import WikiFetcherConfig


class TestWikiFetcherConfig:
    """Test WikiFetcherConfig validation and defaults."""

    def test_default_config(self) -> None:
        """Test that default config values are set correctly."""
        config = WikiFetcherConfig()
        assert str(config.base_url) == "https://ddowiki.com"
        assert config.rate_limit_delay == 2.5
        assert config.max_retries == 3
        assert config.timeout == 30
        assert config.max_concurrent == 3
        assert config.respect_robots_txt is True

    def test_custom_config(self) -> None:
        """Test creating config with custom values."""
        config = WikiFetcherConfig(
            base_url="https://example.com",
            rate_limit_delay=5.0,
            max_retries=5,
            timeout=60,
            max_concurrent=5,
        )
        assert str(config.base_url) == "https://example.com"
        assert config.rate_limit_delay == 5.0
        assert config.max_retries == 5
        assert config.timeout == 60
        assert config.max_concurrent == 5

    def test_invalid_base_url_scheme(self) -> None:
        """Test that non-HTTP(S) URLs are rejected."""
        with pytest.raises(ValidationError):
            WikiFetcherConfig(base_url="ftp://ddowiki.com")

    def test_rate_limit_delay_bounds(self) -> None:
        """Test that rate_limit_delay is bounded correctly."""
        # Too low
        with pytest.raises(ValidationError):
            WikiFetcherConfig(rate_limit_delay=0.5)

        # Too high
        with pytest.raises(ValidationError):
            WikiFetcherConfig(rate_limit_delay=15.0)

        # Valid boundaries
        config_low = WikiFetcherConfig(rate_limit_delay=1.0)
        assert config_low.rate_limit_delay == 1.0

        config_high = WikiFetcherConfig(rate_limit_delay=10.0)
        assert config_high.rate_limit_delay == 10.0

    def test_max_retries_bounds(self) -> None:
        """Test that max_retries is bounded correctly."""
        with pytest.raises(ValidationError):
            WikiFetcherConfig(max_retries=0)

        with pytest.raises(ValidationError):
            WikiFetcherConfig(max_retries=11)

    def test_timeout_bounds(self) -> None:
        """Test that timeout is bounded correctly."""
        with pytest.raises(ValidationError):
            WikiFetcherConfig(timeout=2)

        with pytest.raises(ValidationError):
            WikiFetcherConfig(timeout=150)

    def test_max_concurrent_bounds(self) -> None:
        """Test that max_concurrent is bounded correctly."""
        with pytest.raises(ValidationError):
            WikiFetcherConfig(max_concurrent=0)

        with pytest.raises(ValidationError):
            WikiFetcherConfig(max_concurrent=11)

    def test_base_url_trailing_slash_removal(self) -> None:
        """Test that trailing slashes are removed from base_url."""
        config = WikiFetcherConfig(base_url="https://ddowiki.com/")
        assert str(config.base_url) == "https://ddowiki.com"

        config = WikiFetcherConfig(base_url="https://ddowiki.com///")
        assert str(config.base_url) == "https://ddowiki.com"

    def test_user_agent_validation(self) -> None:
        """Test that user_agent cannot be empty."""
        with pytest.raises(ValidationError):
            WikiFetcherConfig(user_agent="")

        with pytest.raises(ValidationError):
            WikiFetcherConfig(user_agent="   ")

        # Valid user agent
        config = WikiFetcherConfig(user_agent="MyBot/1.0")
        assert config.user_agent == "MyBot/1.0"

    def test_user_agent_whitespace_stripping(self) -> None:
        """Test that user_agent whitespace is stripped."""
        config = WikiFetcherConfig(user_agent="  MyBot/1.0  ")
        assert config.user_agent == "MyBot/1.0"

    def test_get_request_headers(self) -> None:
        """Test that request headers are generated correctly."""
        config = WikiFetcherConfig(user_agent="TestBot/1.0")
        headers = config.get_request_headers()

        assert headers["User-Agent"] == "TestBot/1.0"
        assert "Accept" in headers
        assert "Accept-Language" in headers
        assert "Accept-Encoding" in headers
        assert "DNT" in headers
        assert "Connection" in headers

    def test_config_immutability_disabled(self) -> None:
        """Test that config can be modified after creation."""
        config = WikiFetcherConfig()
        # Should not raise
        config.rate_limit_delay = 5.0
        assert config.rate_limit_delay == 5.0

    def test_validate_assignment(self) -> None:
        """Test that assignment validation works."""
        config = WikiFetcherConfig()

        # Invalid assignment should raise ValidationError
        with pytest.raises(ValidationError):
            config.rate_limit_delay = 0.5  # Too low

        with pytest.raises(ValidationError):
            config.rate_limit_delay = 15.0  # Too high

    def test_extra_fields_forbidden(self) -> None:
        """Test that unknown fields are rejected."""
        with pytest.raises(ValidationError):
            WikiFetcherConfig(unknown_field="value")

    def test_repr(self) -> None:
        """Test that config has reasonable string representation."""
        config = WikiFetcherConfig()
        repr_str = repr(config)
        assert "WikiFetcherConfig" in repr_str
        assert "base_url" in repr_str
