"""Tests for ddo_sync.page_discovery.UpdatePageDiscoverer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from ddo_sync.exceptions import WikiApiError
from ddo_sync.page_discovery import UpdatePageDiscoverer, _extract_update_number


def _api_response(titles: list[str], continue_token: str | None = None) -> dict:
    """Build a minimal allpages API response."""
    resp: dict = {
        "query": {
            "allpages": [{"title": t, "pageid": i + 1} for i, t in enumerate(titles)]
        }
    }
    if continue_token:
        resp["continue"] = {"apcontinue": continue_token}
    return resp


def _mock_get(json_data: dict) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = json_data
    return mock


@pytest.fixture
def discoverer() -> UpdatePageDiscoverer:
    return UpdatePageDiscoverer(timeout=5.0)


class TestDiscover:
    def test_returns_matching_pages(self, discoverer):
        # MediaWiki returns titles with spaces; discoverer normalises to underscores
        data = _api_response(
            [
                "Update 10 named items",
                "Update 11 named items",
                "Update 12 named items",
                "Update 10 release notes",  # should be excluded
                "Update 11 economy changes",  # should be excluded
            ]
        )
        with patch("requests.get", return_value=_mock_get(data)):
            pages = discoverer.discover()
        assert pages == [
            "Update_10_named_items",
            "Update_11_named_items",
            "Update_12_named_items",
        ]

    def test_sorted_numerically(self, discoverer):
        data = _api_response(
            [
                "Update 20 named items",
                "Update 5 named items",
                "Update 100 named items",
            ]
        )
        with patch("requests.get", return_value=_mock_get(data)):
            pages = discoverer.discover()
        assert pages == [
            "Update_5_named_items",
            "Update_20_named_items",
            "Update_100_named_items",
        ]

    def test_returns_empty_list_when_no_matches(self, discoverer):
        data = _api_response(["Update 10 release notes", "Update 11 economy changes"])
        with patch("requests.get", return_value=_mock_get(data)):
            pages = discoverer.discover()
        assert pages == []

    def test_returns_empty_list_when_no_pages(self, discoverer):
        data = _api_response([])
        with patch("requests.get", return_value=_mock_get(data)):
            pages = discoverer.discover()
        assert pages == []

    def test_case_insensitive_pattern(self, discoverer):
        data = _api_response(["update 5 named items"])  # lowercase with spaces
        with patch("requests.get", return_value=_mock_get(data)):
            pages = discoverer.discover()
        assert len(pages) == 1

    def test_underscore_form_also_matches(self, discoverer):
        # Some older pages may already have underscores in the API response
        data = _api_response(["Update_5_named_items"])
        with patch("requests.get", return_value=_mock_get(data)):
            pages = discoverer.discover()
        assert pages == ["Update_5_named_items"]

    def test_output_always_uses_underscores(self, discoverer):
        # Space-form input → underscore-form output
        data = _api_response(["Update 7 named items"])
        with patch("requests.get", return_value=_mock_get(data)):
            pages = discoverer.discover()
        assert pages == ["Update_7_named_items"]
        assert " " not in pages[0]

    def test_follows_continuation(self, discoverer):
        """Two API pages → two requests → combined results."""
        page1 = _api_response(
            ["Update 5 named items", "Update 6 named items"],
            continue_token="Update 7 named items",
        )
        page2 = _api_response(["Update 7 named items", "Update 8 named items"])

        responses = iter([_mock_get(page1), _mock_get(page2)])
        with patch("requests.get", side_effect=lambda *_a, **_kw: next(responses)):
            pages = discoverer.discover()

        assert pages == [
            "Update_5_named_items",
            "Update_6_named_items",
            "Update_7_named_items",
            "Update_8_named_items",
        ]

    def test_continuation_stops_when_no_token(self, discoverer):
        """No 'continue' key in response → single request."""
        data = _api_response(["Update_5_named_items"])
        with patch("requests.get", return_value=_mock_get(data)) as mock_get:
            discoverer.discover()
        mock_get.assert_called_once()

    def test_correct_params_sent(self, discoverer):
        data = _api_response([])
        with patch("requests.get", return_value=_mock_get(data)) as mock_get:
            discoverer.discover()
        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        assert params["action"] == "query"
        assert params["list"] == "allpages"
        assert params["apprefix"] == "Update "
        assert params["apnamespace"] == "0"
        assert params["format"] == "json"

    def test_timeout_passed(self, discoverer):
        data = _api_response([])
        with patch("requests.get", return_value=_mock_get(data)) as mock_get:
            discoverer.discover()
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 5.0

    def test_http_error_raises_wiki_api_error(self, discoverer):
        with patch("requests.get", side_effect=requests.RequestException("down")):
            with pytest.raises(WikiApiError):
                discoverer.discover()

    def test_http_status_error_raises_wiki_api_error(self, discoverer):
        mock = MagicMock()
        mock.raise_for_status.side_effect = requests.HTTPError("503")
        with patch("requests.get", return_value=mock):
            with pytest.raises(WikiApiError):
                discoverer.discover()

    def test_invalid_json_raises_wiki_api_error(self, discoverer):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.side_effect = ValueError("not json")
        with patch("requests.get", return_value=mock):
            with pytest.raises(WikiApiError):
                discoverer.discover()

    def test_missing_query_key_raises_wiki_api_error(self, discoverer):
        data = {"unexpected": "structure"}
        with patch("requests.get", return_value=_mock_get(data)):
            with pytest.raises(WikiApiError):
                discoverer.discover()


class TestExtractUpdateNumber:
    def test_parses_single_digit(self):
        assert _extract_update_number("Update_5_named_items") == 5

    def test_parses_multi_digit(self):
        assert _extract_update_number("Update_100_named_items") == 100

    def test_returns_zero_for_no_digits(self):
        assert _extract_update_number("No_digits_here") == 0
