"""Tests for ddo_sync.wiki_api.WikiApiClient."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from ddo_sync.exceptions import WikiApiError
from ddo_sync.wiki_api import WikiApiClient
from tests.ddo_sync.conftest import make_mediawiki_response, make_missing_page_response

UTC = timezone.utc


@pytest.fixture
def client() -> WikiApiClient:
    return WikiApiClient(timeout=5.0)


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Return a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    return mock


class TestGetLastModified:
    def test_returns_utc_datetime(self, client: WikiApiClient):
        data = make_mediawiki_response("Update_5_named_items", "2025-11-03T18:42:10Z")
        with patch("requests.get", return_value=_mock_response(data)):
            ts = client.get_last_modified("Update_5_named_items")
        assert isinstance(ts, datetime)
        assert ts.tzinfo == UTC

    def test_correct_timestamp(self, client: WikiApiClient):
        data = make_mediawiki_response("Update_5_named_items", "2025-11-03T18:42:10Z")
        with patch("requests.get", return_value=_mock_response(data)):
            ts = client.get_last_modified("Update_5_named_items")
        assert ts == datetime(2025, 11, 3, 18, 42, 10, tzinfo=UTC)

    def test_missing_page_returns_none(self, client: WikiApiClient):
        data = make_missing_page_response("Nonexistent_Page")
        with patch("requests.get", return_value=_mock_response(data)):
            result = client.get_last_modified("Nonexistent_Page")
        assert result is None

    def test_page_with_no_revisions_returns_none(self, client: WikiApiClient):
        data = {"query": {"pages": [{"title": "Empty_Page"}]}}
        with patch("requests.get", return_value=_mock_response(data)):
            result = client.get_last_modified("Empty_Page")
        assert result is None

    def test_http_error_raises_wiki_api_error(self, client: WikiApiClient):
        with patch(
            "requests.get",
            side_effect=requests.RequestException("connection refused"),
        ):
            with pytest.raises(WikiApiError):
                client.get_last_modified("Update_5_named_items")

    def test_invalid_json_raises_wiki_api_error(self, client: WikiApiClient):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.side_effect = ValueError("not json")
        with patch("requests.get", return_value=mock):
            with pytest.raises(WikiApiError):
                client.get_last_modified("Update_5_named_items")

    def test_missing_query_key_raises_wiki_api_error(self, client: WikiApiClient):
        data = {"unexpected": "structure"}
        with patch("requests.get", return_value=_mock_response(data)):
            with pytest.raises(WikiApiError):
                client.get_last_modified("Update_5_named_items")

    def test_http_status_error_raises_wiki_api_error(self, client: WikiApiClient):
        mock = MagicMock()
        mock.raise_for_status.side_effect = requests.HTTPError("503")
        with patch("requests.get", return_value=mock):
            with pytest.raises(WikiApiError):
                client.get_last_modified("Update_5_named_items")

    def test_correct_api_params_sent(self, client: WikiApiClient):
        data = make_mediawiki_response("Update_5_named_items", "2025-01-01T00:00:00Z")
        with patch("requests.get", return_value=_mock_response(data)) as mock_get:
            client.get_last_modified("Update_5_named_items")
        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        assert params["action"] == "query"
        assert params["prop"] == "revisions"
        assert params["titles"] == "Update_5_named_items"
        assert params["format"] == "json"
        assert params["formatversion"] == "2"

    def test_timeout_passed_to_requests(self, client: WikiApiClient):
        data = make_mediawiki_response("Page", "2025-01-01T00:00:00Z")
        with patch("requests.get", return_value=_mock_response(data)) as mock_get:
            client.get_last_modified("Page")
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 5.0


class TestNormalizeTimestamp:
    def test_z_suffix_handled(self, client: WikiApiClient):
        ts = client._normalize_timestamp("2025-11-03T18:42:10Z")
        assert ts == datetime(2025, 11, 3, 18, 42, 10, tzinfo=UTC)

    def test_explicit_offset_handled(self, client: WikiApiClient):
        ts = client._normalize_timestamp("2025-11-03T18:42:10+00:00")
        assert ts == datetime(2025, 11, 3, 18, 42, 10, tzinfo=UTC)

    def test_invalid_string_raises_wiki_api_error(self, client: WikiApiClient):
        with pytest.raises(WikiApiError):
            client._normalize_timestamp("not-a-date")
