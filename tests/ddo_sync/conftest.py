"""Shared fixtures for ddo_sync tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import pytest

from ddo_sync.models import ItemLink
from ddo_sync.queue_db import QueueRepository

# ── Datetime helpers ──────────────────────────────────────────────────────────


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


SYNCED_AT = utc(2025, 11, 1)
MODIFIED_BEFORE = utc(2025, 10, 31)  # wiki older than our sync → no resync
MODIFIED_AFTER = utc(2025, 11, 2)  # wiki newer than our sync → needs resync


# ── QueueRepository fixture ───────────────────────────────────────────────────


@pytest.fixture
def queue_repo() -> QueueRepository:
    """In-memory QueueRepository, open and ready."""
    repo = QueueRepository(":memory:")
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


# ── Minimal HTML fixtures ─────────────────────────────────────────────────────

UPDATE_PAGE_HTML = """
<html>
<body>
  <div id="mw-content-text">
    <p>Named items added in Update 5:</p>
    <ul>
      <li><a href="/page/Item:Sword_of_Shadow">Sword of Shadow</a></li>
      <li><a href="/page/Item:Shield_of_Light">Shield of Light</a></li>
      <li><a href="/page/Item:Ring_of_Fire">Ring of Fire</a></li>
    </ul>
    <!-- duplicate link should be ignored -->
    <a href="/page/Item:Sword_of_Shadow">Sword of Shadow (again)</a>
    <!-- non-item link should be ignored -->
    <a href="/page/Update_5_named_items">Update page itself</a>
  </div>
</body>
</html>
"""

EMPTY_PAGE_HTML = "<html><body><p>No items here.</p></body></html>"


# ── MediaWiki API response fixtures ──────────────────────────────────────────


def make_mediawiki_response(page_name: str, timestamp: str) -> dict:
    """Minimal formatversion=2 response with one revision."""
    return {
        "query": {
            "pages": [
                {
                    "title": page_name,
                    "revisions": [{"timestamp": timestamp}],
                }
            ]
        }
    }


def make_missing_page_response(page_name: str) -> dict:
    return {
        "query": {
            "pages": [
                {
                    "title": page_name,
                    "missing": True,
                }
            ]
        }
    }
