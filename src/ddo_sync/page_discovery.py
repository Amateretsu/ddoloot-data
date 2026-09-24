"""MediaWiki allpages-based discovery of DDO update pages.

Queries the DDO Wiki's MediaWiki Action API to enumerate every page whose
title matches ``Update_<number>_named_items``.  Handles API continuation
transparently so the full list is returned regardless of result-set size.

Example:
    >>> from ddo_sync.page_discovery import UpdatePageDiscoverer
    >>> discoverer = UpdatePageDiscoverer()
    >>> pages = discoverer.discover()
    >>> pages[:3]
    ['Update_10_named_items', 'Update_11_named_items', 'Update_12_named_items']
"""

from __future__ import annotations

import re
from typing import List, Optional

import requests
from loguru import logger

from ddo_sync.exceptions import WikiApiError

# MediaWiki returns titles with spaces ("Update 5 named items"), not underscores.
# This pattern accepts either separator so it works with both forms.
_UPDATE_PAGE_RE = re.compile(r"^Update[\s_]\d+[\s_]named[\s_]items$", re.IGNORECASE)

# MediaWiki normalises underscores → spaces, so "Update " and "Update_" are equivalent.
_ALLPAGES_PREFIX = "Update "

# Maximum rows the MediaWiki API will return in a single allpages call
_API_LIMIT = "max"


class UpdatePageDiscoverer:
    """Discovers DDO Wiki named-item update pages via the MediaWiki API.

    Uses the ``list=allpages`` endpoint with the ``apprefix=Update_`` filter
    to enumerate candidate pages, then applies a regex to keep only those
    that follow the ``Update_<N>_named_items`` naming convention.  API
    continuation is followed automatically.

    Args:
        base_api_url: MediaWiki API endpoint.
        timeout:      Seconds before ``requests.get()`` times out.

    Example:
        >>> discoverer = UpdatePageDiscoverer()
        >>> pages = discoverer.discover()
        >>> all(p.startswith("Update_") for p in pages)
        True
    """

    BASE_API_URL: str = "https://ddowiki.com/api.php"

    def __init__(
        self,
        base_api_url: str = "https://ddowiki.com/api.php",
        timeout: float = 15.0,
    ) -> None:
        self._api_url = base_api_url
        self._timeout = timeout

    def discover(self) -> List[str]:
        """Return all ``Update_<N>_named_items`` page names found on the wiki.

        Queries the MediaWiki ``allpages`` API with the ``Update_`` prefix,
        follows continuation tokens until exhausted, then filters by the
        expected regex pattern.

        Results are sorted numerically by update number.

        Returns:
            Sorted list of page-name strings, e.g.
            ``["Update_10_named_items", "Update_11_named_items", ...]``.

        Raises:
            WikiApiError: If any HTTP request fails or returns invalid JSON.

        Example:
            >>> pages = UpdatePageDiscoverer().discover()
            >>> isinstance(pages, list)
            True
        """
        raw = self._fetch_all_pages()
        # Normalise to underscores so page names are URL-safe (spaces → _)
        matches = [p.replace(" ", "_") for p in raw if _UPDATE_PAGE_RE.match(p)]
        matches.sort(key=_extract_update_number)
        logger.info(f"Discovered {len(matches)} update page(s) matching pattern")
        return matches

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch_all_pages(self) -> List[str]:
        """Walk every allpages continuation block and return raw page titles."""
        titles: List[str] = []
        continue_token: Optional[str] = None

        while True:
            params: dict = {
                "action": "query",
                "list": "allpages",
                "apprefix": _ALLPAGES_PREFIX,
                "apnamespace": "0",
                "aplimit": _API_LIMIT,
                "format": "json",
                "formatversion": "2",
            }
            if continue_token:
                params["apcontinue"] = continue_token

            data = self._get_json(params)

            try:
                pages = data["query"]["allpages"]
            except KeyError as exc:
                raise WikiApiError(
                    f"Unexpected allpages API response structure: missing {exc}",
                    url=self._api_url,
                ) from exc

            batch = [p["title"] for p in pages]
            titles.extend(batch)
            logger.debug(f"allpages batch: {len(batch)} page(s) fetched")

            # Follow continuation if present
            cont = data.get("continue", {})
            continue_token = cont.get("apcontinue")
            if not continue_token:
                break

        logger.debug(
            f"allpages total: {len(titles)} page(s) with prefix {_ALLPAGES_PREFIX!r}"
        )
        if titles:
            logger.debug(f"First few titles: {titles[:5]}")
        return titles

    def _get_json(self, params: dict) -> dict:
        """Execute one GET request against the API and return parsed JSON."""
        try:
            response = requests.get(self._api_url, params=params, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise WikiApiError(
                f"HTTP error querying allpages API: {exc}", url=self._api_url
            ) from exc

        try:
            return response.json()
        except ValueError as exc:
            raise WikiApiError(
                f"Invalid JSON from allpages API: {exc}", url=self._api_url
            ) from exc


def _extract_update_number(page_name: str) -> int:
    """Parse the numeric update index from a page name for sort ordering."""
    match = re.search(r"(\d+)", page_name)
    return int(match.group(1)) if match else 0
