"""Discovery module: which update pages exist and which Named Items each one lists.

Discovery walks rendered ``/page/`` HTML through the Page Store seam
(:class:`~ddo_sync.protocols.PageStoreProtocol`), never the MediaWiki API (ADR 0006):

1. the named-items index page (:data:`NAMED_ITEMS_INDEX_URL`) links to every
   ``Update_<N>_named_items`` page; when it links to none, the committed seed list
   (:data:`UPDATE_PAGES_PATH`, ``config/update_pages.yaml``) names the update pages
   instead;
2. each update page links to the ``Item:`` pages of the Named Items it introduced.

A page the Page Store holds costs no request; ``refresh=True`` refetches it. The revision id
of an update page comes from its HTML (``"wgCurRevisionId":NNN`` in the page's RLCONF
script), since the page HTML is the only source discovery may read.

Interface:

    discover_update_pages(page_store, refresh=False, seed_pages=None) -> list[str]
    read_update_page(page_store, page_name, refresh=False) -> UpdatePage
    update_page_url(page_name) -> str
    update_slug(page_name) -> str

Example:
    >>> pages = discover_update_pages(store)            # ["Update_1_named_items", ...]
    >>> page = read_update_page(store, pages[0])
    >>> page.revision_id, len(page.links)
    (612345, 40)
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import yaml
from bs4 import BeautifulSoup
from loguru import logger

from ddo_sync.exceptions import UpdatePageError
from ddo_sync.models import ItemLink
from ddo_sync.protocols import PageStoreProtocol
from page_store import RunStoppedError

WIKI_BASE_URL = "https://ddowiki.com"
_PAGE_PREFIX = f"{WIKI_BASE_URL}/page/"

#: The wiki page meant to link to every ``Update_<N>_named_items`` page. Checked live on
#: 2026-09-24: it renders ``Category:Items`` and links to no update page, so discovery
#: falls back to :data:`UPDATE_PAGES_PATH`, which was derived from the subcategories of
#: ``Category:Named_items_by_update`` (see the header of that file).
NAMED_ITEMS_INDEX_URL = f"{_PAGE_PREFIX}Named_items"

#: Committed seed list of update page titles, used when the index links to none.
UPDATE_PAGES_PATH = Path(__file__).resolve().parents[2] / "config" / "update_pages.yaml"

# Main-namespace update pages only: not "Category:Update_5_named_items" and not the
# "Update_50_revamped_named_items" pages.
_UPDATE_PAGE_RE = re.compile(r"^Update_(\d+)_named_items$", re.IGNORECASE)
_REVISION_RE = re.compile(r'"wgCurRevisionId":(\d+)')


@dataclass(frozen=True)
class UpdatePage:
    """One update page as read from the Page Store.

    Attributes:
        page_name:   Title with underscores, e.g. ``"Update_5_named_items"``.
        url:         ``https://ddowiki.com/page/<page_name>``.
        revision_id: ``wgCurRevisionId`` from the page HTML, or ``None`` when absent.
        links:       Item links in document order, deduplicated by URL.
    """

    page_name: str
    url: str
    revision_id: Optional[int]
    links: List[ItemLink]


def update_page_url(page_name: str) -> str:
    """The ``/page/`` URL of an update page; spaces in *page_name* become underscores."""
    return _PAGE_PREFIX + page_name.replace(" ", "_")


def update_slug(page_name: Optional[str]) -> str:
    """Results folder name for an update page: ``Update_8_named_items`` -> ``update-8``.

    Anything that is not an ``Update_<N>_named_items`` title (or ``None``) is ``unknown``.
    """
    match = _UPDATE_PAGE_RE.match((page_name or "").replace(" ", "_"))
    return f"update-{int(match.group(1))}" if match else "unknown"


def discover_update_pages(
    page_store: PageStoreProtocol,
    refresh: bool = False,
    seed_pages: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return every ``Update_<N>_named_items`` page the named-items index links to.

    When the index links to no update page, the seed list is used instead.

    Args:
        page_store: Satisfies :class:`~ddo_sync.protocols.PageStoreProtocol`.
        refresh: Refetch the index page even if the Page Store holds it.
        seed_pages: Update page titles to fall back on; ``None`` reads the committed
            list at :data:`UPDATE_PAGES_PATH`.

    Returns:
        Page names with underscores, deduplicated, sorted by update number.

    Raises:
        UpdatePageError: The index page could not be fetched, or neither the index nor
            the seed list names an update page.
        page_store.RunStoppedError: The Page Store says stop the run.
    """
    html = _get(page_store, NAMED_ITEMS_INDEX_URL, refresh)
    pages = _update_pages(title for title, _url in _page_links(html))
    if pages:
        logger.info(f"Discovered {len(pages)} update page(s)")
        return pages
    if seed_pages is None:
        seed_pages = _read_seed_list(UPDATE_PAGES_PATH)
    pages = _update_pages(title.replace(" ", "_") for title in seed_pages)
    if not pages:
        raise UpdatePageError(
            "The named-items index links to no Update_<N>_named_items page and the "
            "seed list names none",
            page_url=NAMED_ITEMS_INDEX_URL,
        )
    logger.warning(
        "The named-items index links to no update page; using the "
        f"{len(pages)} update page(s) of the seed list."
    )
    return pages


def read_update_page(
    page_store: PageStoreProtocol, page_name: str, refresh: bool = False
) -> UpdatePage:
    """Read one update page through the Page Store and list its ``Item:`` links.

    Args:
        page_store: Satisfies :class:`~ddo_sync.protocols.PageStoreProtocol`.
        page_name: Update page title (spaces or underscores).
        refresh: Refetch the page even if the Page Store holds it.

    Raises:
        UpdatePageError: The page could not be fetched or its HTML is empty.
        page_store.RunStoppedError: The Page Store says stop the run.
    """
    name = page_name.replace(" ", "_")
    url = update_page_url(name)
    html = _get(page_store, url, refresh)
    links: dict[str, ItemLink] = {}
    for title, wiki_url in _page_links(html):
        if not title.startswith("Item:"):
            continue
        item_name = title.removeprefix("Item:").replace("_", " ").strip()
        if item_name and wiki_url not in links:
            links[wiki_url] = ItemLink(
                item_name=item_name, wiki_url=wiki_url, update_page=name
            )
    match = _REVISION_RE.search(html)
    revision_id = int(match.group(1)) if match else None
    logger.debug(f"{name!r} (revision {revision_id}): {len(links)} item link(s)")
    return UpdatePage(
        page_name=name, url=url, revision_id=revision_id, links=list(links.values())
    )


def _update_pages(titles: Iterable[str]) -> List[str]:
    """The ``Update_<N>_named_items`` titles among *titles*, deduplicated, by update."""
    numbers: dict[str, int] = {}
    for title in titles:
        match = _UPDATE_PAGE_RE.match(title)
        if match:
            numbers.setdefault(title, int(match.group(1)))
    return sorted(numbers, key=lambda name: numbers[name])


def _read_seed_list(path: Path) -> List[str]:
    try:
        titles = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError) as exc:
        raise UpdatePageError(
            f"Cannot read the update page seed list {path}: {exc}",
            page_url=NAMED_ITEMS_INDEX_URL,
        ) from exc
    if not isinstance(titles, list):
        raise UpdatePageError(
            f"The update page seed list {path} must be a list of titles",
            page_url=NAMED_ITEMS_INDEX_URL,
        )
    return [str(title) for title in titles]


def _get(page_store: PageStoreProtocol, url: str, refresh: bool) -> str:
    try:
        html = page_store.get(url, refresh=refresh).html
    except RunStoppedError:
        raise
    except Exception as exc:
        raise UpdatePageError(f"Could not read {url}: {exc}", page_url=url) from exc
    if not html or not html.strip():
        raise UpdatePageError(f"Empty HTML for {url}", page_url=url)
    return html


def _page_links(html: str) -> List[Tuple[str, str]]:
    """``(decoded title, absolute URL)`` of every ``/page/<title>`` link, in document order.

    Only the article body (``#mw-content-text``) is read, so the skin's tabs, sidebar and
    footer never count; HTML without that element is read whole. Relative and absolute
    wiki hrefs both count and keep the wiki's own percent-encoding; fragments are dropped;
    links with a query string (edit links, red links) are skipped.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find(id="mw-content-text") or soup
    found = []
    for tag in body.find_all("a", href=True):
        href = tag["href"].removeprefix(WIKI_BASE_URL).split("#", 1)[0]
        if not href.startswith("/page/") or "?" in href:
            continue
        title = urllib.parse.unquote(href[len("/page/") :]).replace(" ", "_")
        if title:
            found.append((title, WIKI_BASE_URL + href))
    return found
