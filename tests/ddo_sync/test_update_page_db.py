"""Tests for ddo_sync.update_page_db.UpdatePageRepository."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from ddo_sync.exceptions import QueueDbError, QueueSchemaError
from ddo_sync.models import UpdatePageStatus
from ddo_sync.update_page_db import UpdatePageRepository, _utcnow
from tests.ddo_sync.conftest import (
    MODIFIED_AFTER,
    MODIFIED_BEFORE,
    SYNCED_AT,
)

PAGE_NAME = "Update_5_named_items"
PAGE_URL = "https://ddowiki.com/page/Update_5_named_items"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> UpdatePageRepository:
    """In-memory UpdatePageRepository, open and ready."""
    r = UpdatePageRepository(":memory:")
    r.open()
    yield r
    r.close()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _register(repo: UpdatePageRepository) -> None:
    repo.register(PAGE_NAME, PAGE_URL)


def _make_error_conn() -> MagicMock:
    """Return a MagicMock connection whose execute always raises sqlite3.Error."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False
    mock_conn.execute.side_effect = sqlite3.Error("db error")
    return mock_conn


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_open_and_close(self):
        r = UpdatePageRepository(":memory:")
        r.open()
        r.close()

    def test_context_manager(self):
        with UpdatePageRepository(":memory:") as r:
            r.register(PAGE_NAME, PAGE_URL)

    def test_double_open_is_idempotent(self):
        r = UpdatePageRepository(":memory:")
        r.open()
        r.open()
        r.close()

    def test_double_close_is_idempotent(self):
        r = UpdatePageRepository(":memory:")
        r.open()
        r.close()
        r.close()

    def test_auto_open_on_first_use(self):
        r = UpdatePageRepository(":memory:")
        r.register(PAGE_NAME, PAGE_URL)
        r.close()


# ── Register ──────────────────────────────────────────────────────────────────


class TestRegister:
    def test_registers_page(self, repo):
        _register(repo)
        pages = repo.list_all()
        assert len(pages) == 1
        assert pages[0].page_name == PAGE_NAME

    def test_idempotent_second_register(self, repo):
        _register(repo)
        _register(repo)
        assert len(repo.list_all()) == 1

    def test_initial_timestamps_are_none(self, repo):
        _register(repo)
        status = repo.get(PAGE_NAME)
        assert status.last_synced_at is None
        assert status.wiki_modified_at is None

    def test_initial_needs_resync_true(self, repo):
        _register(repo)
        status = repo.get(PAGE_NAME)
        assert status.needs_resync is True


# ── mark_synced ───────────────────────────────────────────────────────────────


class TestMarkSynced:
    def test_stores_timestamp(self, repo):
        _register(repo)
        repo.mark_synced(PAGE_NAME, SYNCED_AT)
        assert repo.get(PAGE_NAME).last_synced_at == SYNCED_AT

    def test_needs_resync_false_when_synced_after_modified(self, repo):
        _register(repo)
        repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_BEFORE)
        repo.mark_synced(PAGE_NAME, SYNCED_AT)
        assert repo.get(PAGE_NAME).needs_resync is False

    def test_needs_resync_true_when_modified_after_synced(self, repo):
        _register(repo)
        repo.mark_synced(PAGE_NAME, SYNCED_AT)
        repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_AFTER)
        assert repo.get(PAGE_NAME).needs_resync is True


# ── set_wiki_modified_at ──────────────────────────────────────────────────────


class TestSetWikiModifiedAt:
    def test_stores_timestamp(self, repo):
        _register(repo)
        repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_AFTER)
        assert repo.get(PAGE_NAME).wiki_modified_at == MODIFIED_AFTER

    def test_stores_none(self, repo):
        _register(repo)
        repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_AFTER)
        repo.set_wiki_modified_at(PAGE_NAME, None)
        assert repo.get(PAGE_NAME).wiki_modified_at is None

    def test_none_triggers_resync(self, repo):
        _register(repo)
        repo.mark_synced(PAGE_NAME, SYNCED_AT)
        repo.set_wiki_modified_at(PAGE_NAME, None)
        assert repo.get(PAGE_NAME).needs_resync is True


# ── get ───────────────────────────────────────────────────────────────────────


class TestGet:
    def test_returns_none_for_unknown_page(self, repo):
        assert repo.get("Nonexistent_Page") is None

    def test_returns_status_for_known_page(self, repo):
        _register(repo)
        status = repo.get(PAGE_NAME)
        assert isinstance(status, UpdatePageStatus)
        assert status.page_name == PAGE_NAME
        assert status.page_url == PAGE_URL


# ── list_all ──────────────────────────────────────────────────────────────────


class TestListAll:
    def test_empty_when_none_registered(self, repo):
        assert repo.list_all() == []

    def test_returns_all_pages(self, repo):
        repo.register("Page_A", "https://ddowiki.com/page/Page_A")
        repo.register("Page_B", "https://ddowiki.com/page/Page_B")
        assert len(repo.list_all()) == 2

    def test_ordered_alphabetically(self, repo):
        repo.register("Zebra_Page", "https://ddowiki.com/page/Zebra_Page")
        repo.register("Alpha_Page", "https://ddowiki.com/page/Alpha_Page")
        pages = repo.list_all()
        assert pages[0].page_name == "Alpha_Page"
        assert pages[1].page_name == "Zebra_Page"


# ── Exception paths ───────────────────────────────────────────────────────────


class TestDbErrors:
    def test_open_schema_error(self):
        with patch(
            "ddo_sync.update_page_db.sqlite3.connect",
            side_effect=sqlite3.Error("disk full"),
        ):
            with pytest.raises(QueueSchemaError):
                r = UpdatePageRepository(":memory:")
                r.open()

    def test_close_commit_error_is_swallowed(self, repo):
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.commit.side_effect = sqlite3.Error("disk full")
        repo._conn = mock_conn
        repo.close()  # Should not raise

    def test_register_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.register(PAGE_NAME, PAGE_URL)

    def test_mark_synced_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.mark_synced(PAGE_NAME, SYNCED_AT)

    def test_set_wiki_modified_at_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.set_wiki_modified_at(PAGE_NAME, MODIFIED_AFTER)

    def test_get_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.get(PAGE_NAME)

    def test_list_all_raises_queue_db_error(self, repo):
        repo._conn = _make_error_conn()
        with pytest.raises(QueueDbError):
            repo.list_all()

    def test_utcnow_returns_datetime(self):
        result = _utcnow()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None
