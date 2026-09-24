"""Shared fixtures for ddo_sync tests."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from ddo_sync.models import ItemLink
from ddo_sync.queue_db import QueueRepository
from item_extractor import ScrapedItem
from page_store import CachedPage

PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "pages"
ITEM_PAGE_HTML = (PAGES / "Item_Breaker_of_Bodies.html").read_text(encoding="utf-8")


class InMemoryItemWriter:
    """In-memory adapter for ScrapedItemWriterProtocol."""

    def __init__(self) -> None:
        self.written: list[tuple[ScrapedItem, dict[str, Any]]] = []

    def write(self, item: ScrapedItem, report: dict[str, Any]) -> None:
        self.written.append((item, report))


class InMemoryPageStore:
    """In-memory adapter for PageStoreProtocol.

    *serve* plays the wiki: it returns the HTML for a URL, or raises. Pages it served are
    held, so a second ``get`` of the same URL makes no request unless ``refresh=True``.
    ``requests`` lists every URL that reached the "wiki".
    """

    def __init__(self, serve: Callable[[str], str]) -> None:
        self.serve = serve
        self.held: Dict[str, CachedPage] = {}
        self.requests: List[str] = []

    def get(self, url: str, refresh: bool = False) -> CachedPage:
        if url in self.held and not refresh:
            return self.held[url]
        self.requests.append(url)
        html = self.serve(url)
        page = CachedPage(
            html=html,
            url=url,
            title=url.rsplit("/page/", 1)[-1].replace("_", " "),
            fetched_at=datetime.now(timezone.utc),
            via="plain",
        )
        self.held[url] = page
        return page


def wiki_page(title: str, content: str, revision_id: int | None = 700001) -> str:
    """Page *title* in the real wiki skin (a committed fixture page) with *content*.

    The skin's tabs, sidebar, footer and category links stay, so tests see the same noise
    real MediaWiki output carries. ``revision_id=None`` drops ``wgCurRevisionId``.
    """
    skin = ITEM_PAGE_HTML.replace("Item:Breaker_of_Bodies", title).replace(
        "Item:Breaker of Bodies", title.replace("_", " ")
    )
    start = skin.index(_CONTENT_OPEN) + len(_CONTENT_OPEN)
    end = skin.index("<!-- \nNewPP limit report", start)
    html = skin[:start] + content + skin[end:]
    revision = f'"wgCurRevisionId":{revision_id}' if revision_id is not None else ""
    return re.sub(r'"wgCurRevisionId":\d+', revision, html)


_CONTENT_OPEN = '<div class="mw-content-ltr mw-parser-output" lang="en" dir="ltr">'

INDEX_URL = "https://ddowiki.com/page/Named_items"

NAMED_ITEMS_INDEX_HTML = wiki_page(
    "Named_items",
    """
<p>Named items, listed by the update that introduced them.</p>
<table class="wikitable">
<tr><th>Update</th><th>Named items</th></tr>
<tr><td>Update 10</td>
<td><a href="/page/Update_10_named_items" title="Update 10 named items">Update 10 named
items</a></td></tr>
<tr><td>Update 5</td>
<td><a href="/page/Update_5_named_items" title="Update 5 named items">Update 5 named
items</a></td></tr>
<tr><td>Update 8</td>
<td><a href="/page/Update_8_named_items#Weapons" title="Update 8 named items">Update 8
named items</a></td></tr>
<tr><td>Update 50 (revamped)</td>
<td><a href="/page/Update_50_revamped_named_items">Update 50 revamped</a></td></tr>
<tr><td>Update 99</td>
<td><a href="/index.php?title=Update_99_named_items&amp;action=edit&amp;redlink=1"
class="new" title="Update 99 named items (page does not exist)">Update 99</a></td></tr>
</table>
<p>See also <a href="/page/Category:Update_5_named_items">Category:Update 5 named
items</a> and <a href="/page/Update_5_named_items">Update 5</a> again.</p>
""",
)

UPDATE_PAGE_HTML = wiki_page(
    "Update_5_named_items",
    """
<p>Named items added in <a href="/page/Update_5">Update 5</a>:</p>
<table class="wikitable sortable">
<tr><th>Name</th><th>Level</th></tr>
<tr><td><a href="/page/Item:Sword_of_Shadow" title="Item:Sword of Shadow">Sword of
Shadow</a></td><td>12</td></tr>
<tr><td><a href="/page/Item:Shield_of_Light" title="Item:Shield of Light">Shield of
Light</a></td><td>10</td></tr>
<tr><td><a href="/page/Item:Ring_of_Fire#Upgrades" title="Item:Ring of Fire">Ring of
Fire</a></td><td>8</td></tr>
</table>
<p><a href="/page/Item:Sword_of_Shadow">Sword of Shadow (again)</a>,
<a href="/index.php?title=Item:Missing_Blade&amp;action=edit&amp;redlink=1" class="new">
Missing Blade</a>, <a href="/page/Update_5_named_items">this page</a>.</p>
""",
    revision_id=628001,
)


def serve_wiki(url: str) -> str:
    """The index, update pages (all with the same links) and a real item page."""
    if url == INDEX_URL:
        return NAMED_ITEMS_INDEX_HTML
    return ITEM_PAGE_HTML if "/page/Item:" in url else UPDATE_PAGE_HTML


# ── Datetime helpers ──────────────────────────────────────────────────────────


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


SYNCED_AT = utc(2025, 11, 1)


# ── QueueRepository fixture ───────────────────────────────────────────────────


@pytest.fixture
def queue_repo(tmp_path) -> QueueRepository:
    """QueueRepository over a temporary SQLite file, open and ready."""
    repo = QueueRepository(str(tmp_path / "queue.db"))
    repo.open()
    yield repo
    repo.close()


# ── ItemLink fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def sword_link() -> ItemLink:
    return ItemLink(
        item_name="Sword of Shadow",
        wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow",
        update_page="Update_5_named_items",
    )


@pytest.fixture
def shield_link() -> ItemLink:
    return ItemLink(
        item_name="Shield of Light",
        wiki_url="https://ddowiki.com/page/Item:Shield_of_Light",
        update_page="Update_5_named_items",
    )


@pytest.fixture
def item_links(sword_link: ItemLink, shield_link: ItemLink) -> List[ItemLink]:
    return [sword_link, shield_link]
