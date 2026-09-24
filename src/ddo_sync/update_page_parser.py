"""Parses DDO Wiki update page HTML to extract named item links.

Example:
    >>> from ddo_sync.update_page_parser import UpdatePageParser
    >>> parser = UpdatePageParser()
    >>> links = parser.parse(html, page_name="Update_5_named_items")
    >>> links[0].item_name
    'Sword of Shadow'
"""

from __future__ import annotations

import re
import urllib.parse
from typing import List

from bs4 import BeautifulSoup
from loguru import logger

from ddo_sync.exceptions import UpdatePageError
from ddo_sync.models import ItemLink

# Matches MediaWiki item page hrefs: /page/Item:SomeName
_ITEM_HREF_RE = re.compile(r"^/page/Item:", re.IGNORECASE)


class UpdatePageParser:
    """Parses the HTML of a DDO Wiki update page to extract item links.

    Scans all anchor tags whose href matches ``/page/Item:...``. The href is
    URL-decoded and underscores are replaced with spaces to produce the
    display name. Results are deduplicated by URL, preserving document order.

    Args:
        base_url: Wiki base used to resolve relative hrefs. Defaults to the
                  live DDO Wiki.

    Example:
        >>> parser = UpdatePageParser()
        >>> links = parser.parse(html, page_name="Update_5_named_items")
        >>> len(links)
        12
    """

    def __init__(self, base_url: str = "https://ddowiki.com") -> None:
        self._base_url = base_url.rstrip("/")

    def parse(self, html: str, page_name: str) -> List[ItemLink]:
        """Extract all ``Item:`` links from an update page HTML string.

        Args:
            html:      Raw HTML string of the update page.
            page_name: The ``update_pages.page_name`` key (e.g.
                       ``"Update_5_named_items"``). Injected verbatim into
                       each returned :class:`ItemLink`.

        Returns:
            Deduplicated list of :class:`ItemLink` objects in document order.
            Returns an empty list if no ``Item:`` links are found.

        Raises:
            UpdatePageError: If ``html`` is empty.

        Example:
            >>> parser.parse(html, "Update_5_named_items")
            [ItemLink(item_name='Sword of Shadow', wiki_url='...',
             update_page='Update_5_named_items')]
        """
        if not html or not html.strip():
            raise UpdatePageError(
                f"Empty HTML received for update page: {page_name!r}",
                page_url=f"{self._base_url}/page/{page_name}",
            )

        soup = BeautifulSoup(html, "html.parser")

        seen_urls: dict[str, ItemLink] = {}

        for tag in soup.find_all("a", href=_ITEM_HREF_RE):
            href = tag.get("href", "")
            if not href:
                continue

            wiki_url = self._base_url + href
            if wiki_url in seen_urls:
                continue

            # Decode URL encoding (%27 → ') and convert underscores to spaces.
            raw_name = href.split("/page/Item:", 1)[-1]
            item_name = urllib.parse.unquote(raw_name).replace("_", " ").strip()

            if not item_name:
                continue

            link = ItemLink(
                item_name=item_name,
                wiki_url=wiki_url,
                update_page=page_name,
            )
            seen_urls[wiki_url] = link

        links = list(seen_urls.values())
        logger.debug(f"Parsed {len(links)} item links from {page_name!r}")
        return links
