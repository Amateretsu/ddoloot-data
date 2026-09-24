"""Tests for ddo_sync.models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ddo_sync.models import (
    ItemLink,
    QueueItem,
    QueueStats,
    SyncStatus,
    UpdatePageStatus,
)

UTC = timezone.utc


class TestItemLink:
    def test_fields(self):
        link = ItemLink(
            item_name="Sword of Shadow",
            wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow",
            update_page="Update_5_named_items",
        )
        assert link.item_name == "Sword of Shadow"
        assert link.wiki_url == "https://ddowiki.com/page/Item:Sword_of_Shadow"
        assert link.update_page == "Update_5_named_items"

    def test_frozen(self):
        link = ItemLink("A", "http://x", "Page")
        with pytest.raises((TypeError, AttributeError)):
            link.item_name = "B"  # type: ignore[misc]


class TestQueueStats:
    def test_total(self):
        stats = QueueStats(pending=5, in_progress=2, complete=10, failed=1, skipped=0)
        assert stats.total == 18

    def test_total_all_zeros(self):
        assert QueueStats(0, 0, 0, 0, 0).total == 0

    def test_frozen(self):
        stats = QueueStats(1, 0, 0, 0, 0)
        with pytest.raises((TypeError, AttributeError)):
            stats.pending = 99  # type: ignore[misc]


class TestUpdatePageStatus:
    def _make(self, last_synced=None, wiki_modified=None):
        return UpdatePageStatus(
            page_name="Update_5_named_items",
            page_url="https://ddowiki.com/page/Update_5_named_items",
            last_synced_at=last_synced,
            wiki_modified_at=wiki_modified,
        )

    def test_needs_resync_when_both_none(self):
        status = self._make()
        assert status.needs_resync is True

    def test_needs_resync_true_when_last_synced_none(self):
        status = self._make(wiki_modified=datetime(2025, 11, 1, tzinfo=UTC))
        assert status.needs_resync is True

    def test_needs_resync_true_when_wiki_modified_none(self):
        status = self._make(last_synced=datetime(2025, 11, 1, tzinfo=UTC))
        assert status.needs_resync is True

    def test_needs_resync_false_when_synced_after_modified(self):
        synced = datetime(2025, 11, 2, tzinfo=UTC)
        modified = datetime(2025, 11, 1, tzinfo=UTC)
        status = self._make(last_synced=synced, wiki_modified=modified)
        assert status.needs_resync is False

    def test_needs_resync_true_when_modified_after_synced(self):
        synced = datetime(2025, 11, 1, tzinfo=UTC)
        modified = datetime(2025, 11, 2, tzinfo=UTC)
        status = self._make(last_synced=synced, wiki_modified=modified)
        assert status.needs_resync is True

    def test_frozen(self):
        status = self._make()
        with pytest.raises((TypeError, AttributeError)):
            status.page_name = "other"  # type: ignore[misc]


class TestSyncStatus:
    def test_fields(self):
        stats = QueueStats(pending=1, in_progress=0, complete=5, failed=0, skipped=0)
        page = UpdatePageStatus(
            page_name="Update_5_named_items",
            page_url="https://ddowiki.com/page/Update_5_named_items",
            last_synced_at=None,
            wiki_modified_at=None,
        )
        status = SyncStatus(
            queue_stats=stats, update_pages={"Update_5_named_items": page}
        )
        assert status.queue_stats.total == 6
        assert "Update_5_named_items" in status.update_pages

    def test_frozen(self):
        stats = QueueStats(0, 0, 0, 0, 0)
        sync = SyncStatus(queue_stats=stats, update_pages={})
        with pytest.raises((TypeError, AttributeError)):
            sync.queue_stats = stats  # type: ignore[misc]


class TestQueueItem:
    def test_fields(self):
        now = datetime(2025, 1, 1, tzinfo=UTC)
        item = QueueItem(
            id=1,
            item_name="Sword of Shadow",
            wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow",
            update_page="Update_5_named_items",
            status="pending",
            queued_at=now,
            started_at=None,
            completed_at=None,
            error_message=None,
            retry_count=0,
        )
        assert item.id == 1
        assert item.status == "pending"
        assert item.retry_count == 0
        assert item.error_message is None

    def test_frozen(self):
        now = datetime(2025, 1, 1, tzinfo=UTC)
        item = QueueItem(
            1, "A", "http://x", "Page", "pending", now, None, None, None, 0
        )
        with pytest.raises((TypeError, AttributeError)):
            item.status = "complete"  # type: ignore[misc]
