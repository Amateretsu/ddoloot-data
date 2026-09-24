"""Tests for ddo_sync.scrape_queue_db.ScrapeQueueRepository."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from ddo_sync.exceptions import QueueDbError, QueueSchemaError
from ddo_sync.models import ItemLink, QueueItem, QueueStats
from ddo_sync.scrape_queue_db import ScrapeQueueRepository
from tests.ddo_sync.conftest import utc

PAGE_NAME = "Update_5_named_items"
PAGE_URL = "https://ddowiki.com/page/Update_5_named_items"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> ScrapeQueueRepository:
    """In-memory ScrapeQueueRepository with the update_pages row pre-inserted."""
    r = ScrapeQueueRepository(":memory:")
    r.open()
    # scrape_queue has a FK to update_pages; seed it directly
    with r._get_conn() as conn:
        conn.execute(
            "INSERT INTO update_pages (page_name, page_url) VALUES (?, ?)",
            (PAGE_NAME, PAGE_URL),
        )
    yield r
    r.close()


@pytest.fixture
def sword_link() -> ItemLink:
    return ItemLink(
        item_name="Sword of Shadow",
        wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow",
        update_page=PAGE_NAME,
    )


@pytest.fixture
def shield_link() -> ItemLink:
    return ItemLink(
        item_name="Shield of Light",
        wiki_url="https://ddowiki.com/page/Item:Shield_of_Light",
        update_page=PAGE_NAME,
    )


@pytest.fixture
def item_links(sword_link, shield_link):
    return [sword_link, shield_link]


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_open_and_close(self):
        r = ScrapeQueueRepository(":memory:")
        r.open()
        r.close()

    def test_context_manager(self):
        with ScrapeQueueRepository(":memory:") as r:
            stats = r.get_queue_stats()
            assert stats.total == 0

    def test_double_open_is_idempotent(self):
        r = ScrapeQueueRepository(":memory:")
        r.open()
        r.open()
        r.close()

    def test_double_close_is_idempotent(self):
        r = ScrapeQueueRepository(":memory:")
        r.open()
        r.close()
        r.close()

    def test_auto_open_on_first_use(self):
        r = ScrapeQueueRepository(":memory:")
        stats = r.get_queue_stats()
        assert stats.total == 0
        r.close()


# ── enqueue_items ─────────────────────────────────────────────────────────────


class TestEnqueueItems:
    def test_inserts_new_items(self, repo, item_links):
        assert repo.enqueue_items(item_links) == 2

    def test_returns_zero_for_empty_list(self, repo):
        assert repo.enqueue_items([]) == 0

    def test_duplicate_same_page_ignored(self, repo, item_links):
        assert repo.enqueue_items(item_links) == 2
        assert repo.enqueue_items(item_links) == 0

    def test_items_default_to_pending(self, repo, item_links):
        repo.enqueue_items(item_links)
        assert all(i.status == "pending" for i in repo.get_pending_items())

    def test_same_item_different_page_allowed(self, repo, sword_link):
        with repo._get_conn() as conn:
            conn.execute(
                "INSERT INTO update_pages (page_name, page_url) VALUES (?, ?)",
                ("Page_B", "https://ddowiki.com/page/Page_B"),
            )
        link_b = ItemLink(sword_link.item_name, sword_link.wiki_url, "Page_B")
        assert repo.enqueue_items([sword_link]) == 1
        assert repo.enqueue_items([link_b]) == 1


# ── mark_in_progress ──────────────────────────────────────────────────────────


class TestMarkInProgress:
    def test_transitions_to_in_progress(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        repo.mark_in_progress(item.id, utc(2025, 11, 1, 10))
        stats = repo.get_queue_stats()
        assert stats.in_progress == 1
        assert stats.pending == 0


# ── mark_complete ─────────────────────────────────────────────────────────────


class TestMarkComplete:
    def test_transitions_to_complete(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        repo.mark_in_progress(item.id, utc(2025, 11, 1, 10))
        repo.mark_complete(item.id, utc(2025, 11, 1, 11))
        stats = repo.get_queue_stats()
        assert stats.complete == 1
        assert stats.in_progress == 0


# ── mark_failed ───────────────────────────────────────────────────────────────


class TestMarkFailed:
    def test_transitions_to_failed(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        repo.mark_in_progress(item.id, utc(2025, 11, 1, 10))
        repo.mark_failed(item.id, utc(2025, 11, 1, 11), "Timeout")
        assert repo.get_queue_stats().failed == 1

    def test_increments_retry_count(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        repo.mark_failed(item.id, utc(2025, 11, 1), "err1")
        with repo._get_conn() as conn:
            conn.execute(
                "UPDATE scrape_queue SET status = 'pending' WHERE id = ?", (item.id,)
            )
        item2 = repo.get_pending_items()[0]
        repo.mark_failed(item2.id, utc(2025, 11, 2), "err2")
        rows = repo.get_items_for_update_page(PAGE_NAME)
        assert rows[0].retry_count == 2


# ── mark_skipped ──────────────────────────────────────────────────────────────


class TestMarkSkipped:
    def test_transitions_to_skipped(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        repo.mark_skipped(item.id)
        assert repo.get_queue_stats().skipped == 1


# ── reset_failed_to_pending ───────────────────────────────────────────────────


class TestResetFailedToPending:
    def test_resets_below_max_retries(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        repo.mark_failed(item.id, utc(2025, 11, 1), "err")
        count = repo.reset_failed_to_pending(max_retries=3)
        assert count == 1
        assert repo.get_queue_stats().pending == 1

    def test_does_not_reset_at_max_retries(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        with repo._get_conn() as conn:
            conn.execute(
                "UPDATE scrape_queue SET status='failed', retry_count=3 WHERE id=?",
                (item.id,),
            )
        assert repo.reset_failed_to_pending(max_retries=3) == 0
        assert repo.get_queue_stats().failed == 1

    def test_returns_zero_when_nothing_to_reset(self, repo):
        assert repo.reset_failed_to_pending(max_retries=3) == 0


# ── get_pending_items ─────────────────────────────────────────────────────────


class TestGetPendingItems:
    def test_returns_pending_only(self, repo, item_links):
        repo.enqueue_items(item_links)
        pending = repo.get_pending_items()
        repo.mark_complete(pending[0].id, utc(2025, 11, 1))
        assert len(repo.get_pending_items()) == 1

    def test_respects_limit(self, repo, item_links):
        repo.enqueue_items(item_links)
        assert len(repo.get_pending_items(limit=1)) == 1

    def test_returns_queue_items(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        assert isinstance(repo.get_pending_items()[0], QueueItem)

    def test_ordered_fifo(self, repo, item_links):
        repo.enqueue_items(item_links)
        assert len(repo.get_pending_items()) == 2


# ── get_queue_stats ───────────────────────────────────────────────────────────


class TestGetQueueStats:
    def test_all_zeros_when_empty(self, repo):
        assert repo.get_queue_stats() == QueueStats(0, 0, 0, 0, 0)

    def test_counts_by_status(self, repo, item_links):
        repo.enqueue_items(item_links)
        items = repo.get_pending_items()
        repo.mark_complete(items[0].id, utc(2025, 11, 1))
        stats = repo.get_queue_stats()
        assert stats.pending == 1
        assert stats.complete == 1
        assert stats.total == 2


# ── get_items_for_update_page ─────────────────────────────────────────────────


class TestGetItemsForUpdatePage:
    def test_returns_items_for_page(self, repo, item_links):
        repo.enqueue_items(item_links)
        assert len(repo.get_items_for_update_page(PAGE_NAME)) == 2

    def test_returns_empty_for_unknown_page(self, repo):
        assert repo.get_items_for_update_page("Nonexistent_Page") == []


# ── Exception paths ───────────────────────────────────────────────────────────


def _make_error_conn() -> MagicMock:
    """Return a MagicMock connection whose execute always raises sqlite3.Error."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False
    mock_conn.execute.side_effect = sqlite3.Error("db error")
    return mock_conn


class TestDbErrors:
    def test_open_schema_error(self):
        with patch(
            "ddo_sync.scrape_queue_db.sqlite3.connect",
            side_effect=sqlite3.Error("disk full"),
        ):
            with pytest.raises(QueueSchemaError):
                r = ScrapeQueueRepository(":memory:")
                r.open()

    def test_close_commit_error_is_swallowed(self, repo):
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.commit.side_effect = sqlite3.Error("disk full")
        repo._conn = mock_conn
        repo.close()  # Should not raise

    def test_enqueue_items_raises_queue_db_error(self, repo, sword_link):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.enqueue_items([sword_link])

    def test_mark_failed_raises_queue_db_error(self, repo, sword_link):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.mark_failed(item.id, utc(2025, 11, 1), "err")

    def test_reset_failed_to_pending_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.reset_failed_to_pending(max_retries=3)

    def test_get_pending_items_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.get_pending_items()

    def test_get_queue_stats_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.get_queue_stats()

    def test_get_items_for_update_page_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.get_items_for_update_page(PAGE_NAME)

    def test_update_status_raises_queue_db_error_via_mark_in_progress(
        self, repo, sword_link
    ):
        repo.enqueue_items([sword_link])
        item = repo.get_pending_items()[0]
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.mark_in_progress(item.id, utc(2025, 11, 1, 10))
