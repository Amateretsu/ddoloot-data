"""MediaWiki Action API client for ddo_sync.

Uses plain requests.get() — not the rate-limited WikiFetcher — because API
calls are rare (one per tracked update page per sync cycle) and return tiny
JSON rather than full page HTML.

Example:
    >>> from ddo_sync.wiki_api import WikiApiClient
    >>> client = WikiApiClient()
    >>> ts = client.get_last_modified("Update_5_named_items")
    >>> ts.isoformat()
    '2025-11-03T18:42:10+00:00'
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import requests
from loguru import logger

from ddo_sync.exceptions import WikiApiError

# MediaWiki returns timestamps with a trailing "Z" for UTC.
# Python 3.9's datetime.fromisoformat() cannot parse "Z", so we replace it.
_UTC_Z_SUFFIX = "Z"
_UTC_OFFSET = "+00:00"


class WikiApiClient:
    """Thin wrapper around the DDO Wiki MediaWiki action API.

    Args:
        base_api_url: Base URL for the MediaWiki API endpoint.
        timeout:      Seconds before a requests.get() call times out.

    Example:
        >>> client = WikiApiClient()
        >>> ts = client.get_last_modified("Update_5_named_items")
        >>> ts.tzinfo is not None
        True
    """

    BASE_API_URL: str = "https://ddowiki.com/api.php"

    def __init__(
        self,
        base_api_url: str = "https://ddowiki.com/api.php",
        timeout: float = 10.0,
    ) -> None:
        self._api_url = base_api_url
        self._timeout = timeout

    def get_last_modified(self, page_name: str) -> Optional[datetime]:
        """Return the UTC datetime of the most recent wiki edit to ``page_name``.

        Calls::

            GET https://ddowiki.com/api.php
                ?action=query&prop=revisions&titles={page_name}
                &rvprop=timestamp&format=json&formatversion=2

        The trailing ``Z`` in MediaWiki timestamps (e.g.
        ``"2025-11-03T18:42:10Z"``) is replaced with ``"+00:00"`` before
        calling :func:`datetime.fromisoformat` for Python 3.9 compatibility.

        Args:
            page_name: Wiki page title, e.g. ``"Update_5_named_items"``.

        Returns:
            Timezone-aware :class:`datetime` in UTC, or ``None`` if the page
            does not exist on the wiki.

        Raises:
            WikiApiError: If the HTTP request fails, the response is not valid
                          JSON, or expected keys are absent.

        Example:
            >>> ts = client.get_last_modified("Update_5_named_items")
            >>> ts.tzinfo
            datetime.timezone.utc
        """
        params = {
            "action": "query",
            "prop": "revisions",
            "titles": page_name,
            "rvprop": "timestamp",
            "format": "json",
            "formatversion": "2",
        }

        try:
            response = requests.get(self._api_url, params=params, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise WikiApiError(
                f"HTTP error querying MediaWiki API for {page_name!r}: {exc}",
                url=self._api_url,
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise WikiApiError(
                f"Invalid JSON from MediaWiki API for {page_name!r}: {exc}",
                url=self._api_url,
            ) from exc

        try:
            pages = data["query"]["pages"]
        except KeyError as exc:
            raise WikiApiError(
                f"Unexpected MediaWiki API response structure for {page_name!r}: missing {exc}",
                url=self._api_url,
            ) from exc

        page = pages[0] if isinstance(pages, list) else next(iter(pages.values()))

        # Page does not exist on the wiki
        if page.get("missing") or "revisions" not in page:
            logger.debug(
                f"MediaWiki API: page {page_name!r} not found or has no revisions"
            )
            return None

        raw_ts = page["revisions"][0]["timestamp"]
        modified_at = self._normalize_timestamp(raw_ts)
        logger.debug(
            f"MediaWiki API: {page_name!r} last modified at {modified_at.isoformat()}"
        )
        return modified_at

    def _normalize_timestamp(self, raw: str) -> datetime:
        """Convert a MediaWiki API timestamp to an aware UTC datetime.

        Args:
            raw: ISO 8601 string from MediaWiki, e.g. ``"2025-11-03T18:42:10Z"``.

        Returns:
            Timezone-aware :class:`datetime` (always UTC).

        Raises:
            WikiApiError: If the string cannot be parsed after normalization.
        """
        normalized = raw
        if normalized.endswith(_UTC_Z_SUFFIX):
            normalized = normalized[:-1] + _UTC_OFFSET
        try:
            return datetime.fromisoformat(normalized).astimezone(timezone.utc)
        except (ValueError, TypeError) as exc:
            raise WikiApiError(
                f"Cannot parse MediaWiki timestamp {raw!r}: {exc}",
                url=self._api_url,
            ) from exc
