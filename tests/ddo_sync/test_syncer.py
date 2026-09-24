"""Tests for ddo_sync.syncer.DDOSyncer."""

from __future__ import annotations

from datetime import timezone
from unittest.mock import MagicMock

import pytest

from ddo_sync.exceptions import UpdatePageError
from ddo_sync.models import ItemLink, SyncStatus
from ddo_sync.queue_db import QueueRepository
from ddo_sync.syncer import DDOSyncer
from page_store import ChallengeError, FetchError
from tests.ddo_sync.conftest import (
    ITEM_PAGE_HTML,
    MODIFIED_AFTER,
    MODIFIED_BEFORE,
    SYNCED_AT,
    InMemoryItemWriter,
    InMemoryPageStore,
    serve_wiki,
    utc,
)

UTC = timezone.utc
PAGE_NAME = "Update_5_named_items"
PAGE_URL = "https://ddowiki.com/page/Update_5_named_items"


def _raise(exc: Exception):
    def serve(_url: str) -> str:
        raise exc

    return serve


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def queue_repo() -> QueueRepository:
    repo = QueueRepository(":memory:")
    repo.open()
    yield repo
    repo.close()


@pytest.fixture
def store() -> InMemoryPageStore:
    return InMemoryPageStore(serve_wiki)


@pytest.fixture
def writer() -> InMemoryItemWriter:
    return InMemoryItemWriter()


@pytest.fixture
def mock_api_client() -> MagicMock:
    client = MagicMock()
    client.get_last_modified.return_value = MODIFIED_BEFORE  # wiki older → no resync
    return client


@pytest.fixture
def syncer(store, writer, queue_repo, mock_api_client) -> DDOSyncer:
    # The mock api client means no real HTTP is made.
    return DDOSyncer(store, writer, queue_repo, api_client=mock_api_client)


# ── Registration ──────────────────────────────────────────────────────────────


class TestRegisterUpdatePage:
    def test_registers_page_in_queue(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        pages = queue_repo.list_update_pages()
        assert any(p.page_name == PAGE_NAME for p in pages)

    def test_spaces_converted_to_underscores(self, syncer, queue_repo):
        syncer.register_update_page("Update 5 named items")
        pages = queue_repo.list_update_pages()
        assert any(p.page_name == "Update_5_named_items" for p in pages)

    def test_safe_to_register_twice(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        syncer.register_update_page(PAGE_NAME)
        assert len(queue_repo.list_update_pages()) == 1

    def test_url_built_correctly(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        pages = queue_repo.list_update_pages()
        assert pages[0].page_url == PAGE_URL


# ── sync_update_page ──────────────────────────────────────────────────────────


class TestSyncUpdatePage:
    def test_fetches_correct_url(self, syncer, store):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        assert store.requests == [PAGE_URL]

    def test_returns_item_links(self, syncer):
        syncer.register_update_page(PAGE_NAME)
        links = syncer.sync_update_page(PAGE_NAME)
        assert isinstance(links, list)
        assert all(isinstance(lnk, ItemLink) for lnk in links)

    def test_items_enqueued_after_sync(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        stats = queue_repo.get_queue_stats()
        assert stats.pending > 0

    def test_marks_page_synced(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.last_synced_at is not None

    def test_fetch_error_raises_update_page_error(self, syncer, store):
        syncer.register_update_page(PAGE_NAME)
        store.serve = _raise(FetchError("network down", url=PAGE_URL))
        with pytest.raises(UpdatePageError):
            syncer.sync_update_page(PAGE_NAME)

    def test_challenge_stops_the_run_instead_of_failing_the_page(self, syncer, store):
        syncer.register_update_page(PAGE_NAME)
        store.serve = _raise(ChallengeError("challenged", url=PAGE_URL))
        with pytest.raises(ChallengeError):
            syncer.sync_update_page(PAGE_NAME)

    def test_spaces_in_page_name_handled(self, syncer, store):
        syncer.register_update_page("Update 5 named items")
        syncer.sync_update_page("Update 5 named items")
        assert store.requests == [PAGE_URL]

    def test_held_update_page_is_not_refetched(self, syncer, store):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        assert store.requests == [PAGE_URL]

    def test_refresh_refetches_held_pages(
        self, store, writer, queue_repo, mock_api_client
    ):
        syncer = DDOSyncer(
            store, writer, queue_repo, api_client=mock_api_client, refresh=True
        )
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        assert store.requests == [PAGE_URL, PAGE_URL]


# ── process_queue ─────────────────────────────────────────────────────────────


class TestProcessQueue:
    def test_returns_zero_zero_when_empty(self, syncer):
        success, failures = syncer.process_queue()
        assert success == 0
        assert failures == 0

    def test_processes_pending_items(self, syncer, queue_repo):  # noqa: ARG002
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        success, failures = syncer.process_queue()
        assert success > 0
        assert failures == 0

    def test_marks_items_complete_on_success(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        syncer.process_queue()
        stats = queue_repo.get_queue_stats()
        assert stats.complete > 0
        assert stats.pending == 0

    def test_marks_items_failed_on_error(self, syncer, queue_repo, store):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        # Make fetch fail for processing
        store.serve = _raise(FetchError("scrape failed", url="x", status=404))
        success, failures = syncer.process_queue()
        assert failures > 0
        assert success == 0
        stats = queue_repo.get_queue_stats()
        assert stats.failed > 0

    def test_limit_parameter_respected(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        total_pending = queue_repo.get_queue_stats().pending
        assert total_pending >= 2  # UPDATE_PAGE_HTML has 3 unique items
        success, _ = syncer.process_queue(limit=1)
        assert success == 1

    def test_writes_one_scraped_item_per_queued_page(self, syncer, writer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        item_count = queue_repo.get_queue_stats().pending
        syncer.process_queue()
        assert len(writer.written) == item_count
        item, report = writer.written[0]
        assert item.name == "Breaker of Bodies"
        assert item.wiki.url.startswith("https://ddowiki.com/page/Item:")
        assert report["template"] == "shield"

    def test_challenge_stops_processing_and_leaves_items_pending(
        self, syncer, writer, queue_repo, store
    ):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        total = queue_repo.get_queue_stats().pending
        store.serve = _raise(ChallengeError("challenged", url="x"))
        with pytest.raises(ChallengeError):
            syncer.process_queue()
        stats = queue_repo.get_queue_stats()
        assert stats.failed == 0
        assert stats.in_progress == 0
        assert stats.pending == total
        assert writer.written == []

    def test_items_already_held_are_not_refetched(self, syncer, queue_repo, store):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        item = queue_repo.get_pending_items()[0]
        store.get(item.wiki_url)
        store.requests.clear()
        syncer.process_queue()
        assert item.wiki_url not in store.requests
        assert len(store.requests) == queue_repo.get_queue_stats().complete - 1

    def test_page_that_cannot_be_extracted_is_marked_failed(
        self, syncer, writer, queue_repo, store
    ):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        total = queue_repo.get_queue_stats().pending
        pages = iter(["<html><body>no infobox</body></html>"])
        store.serve = lambda _url: next(pages, ITEM_PAGE_HTML)
        success, failures = syncer.process_queue()
        assert failures == 1
        assert success == total - 1
        assert len(writer.written) == total - 1
        assert queue_repo.get_queue_stats().failed == 1


# ── get_status ────────────────────────────────────────────────────────────────


class TestGetStatus:
    def test_returns_sync_status(self, syncer):
        status = syncer.get_status()
        assert isinstance(status, SyncStatus)

    def test_queue_stats_reflect_state(self, syncer, queue_repo):  # noqa: ARG002
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        status = syncer.get_status()
        assert status.queue_stats.pending > 0

    def test_update_pages_present(self, syncer):
        syncer.register_update_page(PAGE_NAME)
        status = syncer.get_status()
        assert PAGE_NAME in status.update_pages


# ── sync_all ──────────────────────────────────────────────────────────────────


class TestSyncAll:
    def test_returns_sync_status(self, syncer):
        syncer.register_update_page(PAGE_NAME)
        result = syncer.sync_all()
        assert isinstance(result, SyncStatus)

    def test_syncs_stale_page(self, syncer, mock_api_client, store):
        """Page needs resync when wiki is newer than last sync."""
        syncer.register_update_page(PAGE_NAME)
        # Simulate: we synced before, wiki has been updated since
        syncer._queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)
        mock_api_client.get_last_modified.return_value = MODIFIED_AFTER
        syncer.sync_all()
        # the update page + queue items were read through the store
        assert PAGE_URL in store.requests

    def test_skips_up_to_date_page(self, syncer, mock_api_client, store):
        """Page does NOT need resync when wiki is older than last sync."""
        syncer.register_update_page(PAGE_NAME)
        syncer._queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)
        mock_api_client.get_last_modified.return_value = MODIFIED_BEFORE
        syncer.sync_all()
        # the update page is not read (no pending items either)
        assert store.requests == []

    def test_resets_failed_items_first(self, syncer, queue_repo, mock_api_client):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        items = queue_repo.get_pending_items()
        queue_repo.mark_failed(items[0].id, utc(2025, 11, 1), "err")
        assert queue_repo.get_queue_stats().failed == 1
        # Simulate api says no change so no re-fetch of update page
        mock_api_client.get_last_modified.return_value = MODIFIED_BEFORE
        queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)
        syncer.sync_all()
        # Failed item should have been reset and then processed
        assert queue_repo.get_queue_stats().failed == 0

    def test_update_page_error_does_not_abort_cycle(
        self, syncer, store, mock_api_client
    ):
        """If one update page fails to sync, others still process."""
        syncer.register_update_page("Page_A")
        syncer.register_update_page("Page_B")
        mock_api_client.get_last_modified.return_value = MODIFIED_AFTER

        def fetch_side_effect(url):
            if "Page_A" in url:
                raise FetchError("Page A broken", url=url)
            return serve_wiki(url)

        store.serve = fetch_side_effect
        # Should not raise even though Page_A fails
        result = syncer.sync_all()
        assert isinstance(result, SyncStatus)

    def test_mark_complete_raises_item_gets_marked_failed(self, syncer, queue_repo):
        """If mark_complete raises, the item is counted as a failure."""
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)

        # Make mark_complete raise for every item
        original_mark_complete = queue_repo.mark_complete

        def raise_on_complete(_item_id, _completed_at):
            raise Exception("persistence failure")

        queue_repo.mark_complete = raise_on_complete

        _success, failures = syncer.process_queue()

        queue_repo.mark_complete = original_mark_complete
        assert failures >= 1

    def test_refresh_wiki_timestamp_api_failure_logs_warning_and_proceeds(
        self, syncer, mock_api_client
    ):
        """If get_last_modified raises, sync_all still completes without error."""
        syncer.register_update_page(PAGE_NAME)
        mock_api_client.get_last_modified.side_effect = Exception("API is down")

        # Should not raise; the exception is caught and logged as a warning
        result = syncer.sync_all()
        assert isinstance(result, SyncStatus)
