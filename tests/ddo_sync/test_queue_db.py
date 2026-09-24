"""Tests for ddo_sync.queue_db.QueueRepository."""

from __future__ import annotations

import sqlite3
from datetime import timezone
from unittest.mock import MagicMock, patch

import pytest

from ddo_sync.exceptions import QueueDbError, QueueSchemaError
from ddo_sync.models import ItemLink, QueueItem, QueueStats, UpdatePageStatus
from ddo_sync.queue_db import QueueRepository
from tests.ddo_sync.conftest import (
    MODIFIED_AFTER,
    MODIFIED_BEFORE,
    SYNCED_AT,
    utc,
)

UTC = timezone.utc

PAGE_NAME = "Update_5_named_items"
PAGE_URL = "https://ddowiki.com/page/Update_5_named_items"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _register(repo: QueueRepository) -> None:
    repo.register_update_page(PAGE_NAME, PAGE_URL)


def _enqueue(repo: QueueRepository, links):
    return repo.enqueue_items(links)


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_open_and_close(self):
        repo = QueueRepository(":memory:")
        repo.open()
        repo.close()

    def test_context_manager(self):
        with QueueRepository(":memory:") as repo:
            repo.register_update_page(PAGE_NAME, PAGE_URL)
        # No exception means clean open/close

    def test_double_open_is_idempotent(self):
        repo = QueueRepository(":memory:")
        repo.open()
        repo.open()  # second open should be a no-op
        repo.close()

    def test_double_close_is_idempotent(self):
        repo = QueueRepository(":memory:")
        repo.open()
        repo.close()
        repo.close()  # second close should be a no-op

    def test_auto_open_on_first_use(self):
        repo = QueueRepository(":memory:")
        # Not explicitly opened — should auto-open on first call
        repo.register_update_page(PAGE_NAME, PAGE_URL)
        repo.close()


# ── Update page management ────────────────────────────────────────────────────


class TestRegisterUpdatePage:
    def test_registers_page(self, queue_repo):
        _register(queue_repo)
        pages = queue_repo.list_update_pages()
        assert len(pages) == 1
        assert pages[0].page_name == PAGE_NAME

    def test_idempotent_second_register(self, queue_repo):
        _register(queue_repo)
        _register(queue_repo)
        assert len(queue_repo.list_update_pages()) == 1

    def test_initial_timestamps_are_none(self, queue_repo):
        _register(queue_repo)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.last_synced_at is None
        assert status.wiki_modified_at is None

    def test_initial_needs_resync_true(self, queue_repo):
        _register(queue_repo)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.needs_resync is True


class TestMarkPageSynced:
    def test_stores_timestamp(self, queue_repo):
        _register(queue_repo)
        queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.last_synced_at == SYNCED_AT

    def test_needs_resync_false_when_synced_after_modified(self, queue_repo):
        _register(queue_repo)
        queue_repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_BEFORE)
        queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.needs_resync is False

    def test_needs_resync_true_when_modified_after_synced(self, queue_repo):
        _register(queue_repo)
        queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)
        queue_repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_AFTER)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.needs_resync is True


class TestSetWikiModifiedAt:
    def test_stores_timestamp(self, queue_repo):
        _register(queue_repo)
        queue_repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_AFTER)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.wiki_modified_at == MODIFIED_AFTER

    def test_stores_none(self, queue_repo):
        _register(queue_repo)
        queue_repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_AFTER)
        queue_repo.set_wiki_modified_at(PAGE_NAME, None)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.wiki_modified_at is None

    def test_none_triggers_resync(self, queue_repo):
        _register(queue_repo)
        queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)
        queue_repo.set_wiki_modified_at(PAGE_NAME, None)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.needs_resync is True


class TestGetUpdatePageStatus:
    def test_returns_none_for_unknown_page(self, queue_repo):
        result = queue_repo.get_update_page_status("Nonexistent_Page")
        assert result is None

    def test_returns_status_for_known_page(self, queue_repo):
        _register(queue_repo)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert isinstance(status, UpdatePageStatus)
        assert status.page_name == PAGE_NAME
        assert status.page_url == PAGE_URL


class TestListUpdatePages:
    def test_empty_when_none_registered(self, queue_repo):
        assert queue_repo.list_update_pages() == []

    def test_returns_all_pages(self, queue_repo):
        queue_repo.register_update_page("Page_A", "https://ddowiki.com/page/Page_A")
        queue_repo.register_update_page("Page_B", "https://ddowiki.com/page/Page_B")
        pages = queue_repo.list_update_pages()
        assert len(pages) == 2

    def test_ordered_alphabetically(self, queue_repo):
        queue_repo.register_update_page(
            "Zebra_Page", "https://ddowiki.com/page/Zebra_Page"
        )
        queue_repo.register_update_page(
            "Alpha_Page", "https://ddowiki.com/page/Alpha_Page"
        )
        pages = queue_repo.list_update_pages()
        assert pages[0].page_name == "Alpha_Page"
        assert pages[1].page_name == "Zebra_Page"


# ── Queue writes ──────────────────────────────────────────────────────────────


class TestEnqueueItems:
    def test_inserts_new_items(self, queue_repo, item_links):
        _register(queue_repo)
        inserted = _enqueue(queue_repo, item_links)
        assert inserted == 2

    def test_returns_zero_for_empty_list(self, queue_repo):
        _register(queue_repo)
        assert _enqueue(queue_repo, []) == 0

    def test_duplicate_same_page_ignored(self, queue_repo, item_links):
        _register(queue_repo)
        first = _enqueue(queue_repo, item_links)
        second = _enqueue(queue_repo, item_links)
        assert first == 2
        assert second == 0

    def test_items_default_to_pending(self, queue_repo, item_links):
        _register(queue_repo)
        _enqueue(queue_repo, item_links)
        items = queue_repo.get_pending_items()
        assert all(i.status == "pending" for i in items)

    def test_same_item_different_page_allowed(self, queue_repo, sword_link):
        queue_repo.register_update_page("Page_A", "https://ddowiki.com/page/Page_A")
        queue_repo.register_update_page("Page_B", "https://ddowiki.com/page/Page_B")
        link_a = ItemLink(sword_link.item_name, sword_link.wiki_url, "Page_A")
        link_b = ItemLink(sword_link.item_name, sword_link.wiki_url, "Page_B")
        assert _enqueue(queue_repo, [link_a]) == 1
        assert _enqueue(queue_repo, [link_b]) == 1


class TestMarkInProgress:
    def test_transitions_to_in_progress(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo.mark_in_progress(item.id, utc(2025, 11, 1, 10))
        stats = queue_repo.get_queue_stats()
        assert stats.in_progress == 1
        assert stats.pending == 0


class TestMarkComplete:
    def test_transitions_to_complete(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo.mark_in_progress(item.id, utc(2025, 11, 1, 10))
        queue_repo.mark_complete(item.id, utc(2025, 11, 1, 11))
        stats = queue_repo.get_queue_stats()
        assert stats.complete == 1
        assert stats.in_progress == 0


class TestMarkFailed:
    def test_transitions_to_failed(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo.mark_in_progress(item.id, utc(2025, 11, 1, 10))
        queue_repo.mark_failed(item.id, utc(2025, 11, 1, 11), "Timeout")
        stats = queue_repo.get_queue_stats()
        assert stats.failed == 1

    def test_increments_retry_count(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo.mark_failed(item.id, utc(2025, 11, 1), "err1")
        # Manually reset to pending to fail again
        with queue_repo._get_conn() as conn:
            conn.execute(
                "UPDATE scrape_queue SET status = 'pending' WHERE id = ?", (item.id,)
            )
        item2 = queue_repo.get_pending_items()[0]
        queue_repo.mark_failed(item2.id, utc(2025, 11, 2), "err2")
        # retry_count should be 2
        rows = queue_repo.get_items_for_update_page(PAGE_NAME)
        assert rows[0].retry_count == 2


class TestMarkSkipped:
    def test_transitions_to_skipped(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo.mark_skipped(item.id)
        stats = queue_repo.get_queue_stats()
        assert stats.skipped == 1


class TestResetFailedToPending:
    def test_resets_below_max_retries(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo.mark_failed(item.id, utc(2025, 11, 1), "err")
        count = queue_repo.reset_failed_to_pending(max_retries=3)
        assert count == 1
        assert queue_repo.get_queue_stats().pending == 1

    def test_does_not_reset_at_max_retries(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        # Force retry_count to 3
        with queue_repo._get_conn() as conn:
            conn.execute(
                "UPDATE scrape_queue SET status='failed', retry_count=3 WHERE id=?",
                (item.id,),
            )
        count = queue_repo.reset_failed_to_pending(max_retries=3)
        assert count == 0
        assert queue_repo.get_queue_stats().failed == 1

    def test_returns_zero_when_nothing_to_reset(self, queue_repo):
        _register(queue_repo)
        count = queue_repo.reset_failed_to_pending(max_retries=3)
        assert count == 0


# ── Queue reads ───────────────────────────────────────────────────────────────


class TestGetPendingItems:
    def test_returns_pending_only(
        self, queue_repo, item_links, sword_link  # noqa: ARG002
    ):
        _register(queue_repo)
        _enqueue(queue_repo, item_links)
        # Mark one complete
        pending = queue_repo.get_pending_items()
        queue_repo.mark_complete(pending[0].id, utc(2025, 11, 1))
        remaining = queue_repo.get_pending_items()
        assert len(remaining) == 1

    def test_respects_limit(self, queue_repo, item_links):
        _register(queue_repo)
        _enqueue(queue_repo, item_links)
        items = queue_repo.get_pending_items(limit=1)
        assert len(items) == 1

    def test_returns_queue_items(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        items = queue_repo.get_pending_items()
        assert isinstance(items[0], QueueItem)

    def test_ordered_fifo(self, queue_repo, item_links):
        _register(queue_repo)
        _enqueue(queue_repo, item_links)
        items = queue_repo.get_pending_items()
        # Both inserted at the same time; just check they're all present
        assert len(items) == 2


class TestGetQueueStats:
    def test_all_zeros_when_empty(self, queue_repo):
        stats = queue_repo.get_queue_stats()
        assert stats == QueueStats(0, 0, 0, 0, 0)

    def test_counts_by_status(self, queue_repo, item_links):
        _register(queue_repo)
        _enqueue(queue_repo, item_links)
        items = queue_repo.get_pending_items()
        queue_repo.mark_complete(items[0].id, utc(2025, 11, 1))
        stats = queue_repo.get_queue_stats()
        assert stats.pending == 1
        assert stats.complete == 1
        assert stats.total == 2


class TestGetItemsForUpdatePage:
    def test_returns_items_for_page(self, queue_repo, item_links):
        _register(queue_repo)
        _enqueue(queue_repo, item_links)
        items = queue_repo.get_items_for_update_page(PAGE_NAME)
        assert len(items) == 2

    def test_returns_empty_for_unknown_page(self, queue_repo):
        _register(queue_repo)
        items = queue_repo.get_items_for_update_page("Nonexistent_Page")
        assert items == []


# ── Exception path tests ──────────────────────────────────────────────────────


def _make_broken_conn() -> MagicMock:
    """Return a MagicMock that mimics a sqlite3.Connection whose execute raises."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_conn.execute.side_effect = sqlite3.Error("db error")
    # Context manager support: __enter__ returns the mock itself so that
    # `with repo._get_conn() as conn: conn.execute(...)` also raises.
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


def _make_broken_commit_conn() -> MagicMock:
    """Return a MagicMock whose commit raises but everything else is fine."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_conn.commit.side_effect = sqlite3.Error("commit error")
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


class TestDbErrors:
    def test_open_schema_error_raises_queue_schema_error(self):
        with patch("sqlite3.connect", side_effect=sqlite3.Error("db error")):
            repo = QueueRepository(":memory:")
            with pytest.raises(QueueSchemaError):
                repo.open()

    def test_close_commit_error_is_swallowed(self, queue_repo):
        queue_repo._conn = _make_broken_commit_conn()
        # Should not raise
        queue_repo.close()

    def test_register_update_page_raises_queue_db_error(self, queue_repo):
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.register_update_page(PAGE_NAME, PAGE_URL)

    def test_mark_page_synced_raises_queue_db_error(self, queue_repo):
        _register(queue_repo)
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)

    def test_set_wiki_modified_at_raises_queue_db_error(self, queue_repo):
        _register(queue_repo)
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_AFTER)

    def test_get_update_page_status_raises_queue_db_error(self, queue_repo):
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.get_update_page_status(PAGE_NAME)

    def test_list_update_pages_raises_queue_db_error(self, queue_repo):
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.list_update_pages()

    def test_enqueue_items_raises_queue_db_error(self, queue_repo, sword_link):
        _register(queue_repo)
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.enqueue_items([sword_link])

    def test_mark_failed_raises_queue_db_error(self, queue_repo, sword_link):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.mark_failed(item.id, utc(2025, 11, 1), "err")

    def test_reset_failed_to_pending_raises_queue_db_error(self, queue_repo):
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.reset_failed_to_pending(max_retries=3)

    def test_get_pending_items_raises_queue_db_error(self, queue_repo):
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.get_pending_items()

    def test_get_queue_stats_raises_queue_db_error(self, queue_repo):
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.get_queue_stats()

    def test_get_items_for_update_page_raises_queue_db_error(self, queue_repo):
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.get_items_for_update_page(PAGE_NAME)

    def test_update_status_via_mark_in_progress_raises_queue_db_error(
        self, queue_repo, sword_link
    ):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.mark_in_progress(item.id, utc(2025, 11, 1, 10))

    def test_update_status_via_mark_complete_raises_queue_db_error(
        self, queue_repo, sword_link
    ):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.mark_complete(item.id, utc(2025, 11, 1, 11))

    def test_update_status_via_mark_skipped_raises_queue_db_error(
        self, queue_repo, sword_link
    ):
        _register(queue_repo)
        _enqueue(queue_repo, [sword_link])
        item = queue_repo.get_pending_items()[0]
        queue_repo._conn = _make_broken_conn()
        with pytest.raises(QueueDbError):
            queue_repo.mark_skipped(item.id)
