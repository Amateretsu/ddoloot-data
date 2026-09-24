"""Tests for ddo_sync.syncer.DDOSyncer."""

from __future__ import annotations

import json
import sys
from datetime import timezone

import pytest

from catalog_registry import Registry
from ddo_sync import CatalogWriter
from ddo_sync.exceptions import UpdatePageError
from ddo_sync.models import ItemLink, SyncStatus
from ddo_sync.syncer import DDOSyncer
from page_store import (
    BrowserPolicy,
    ChallengeError,
    FetchError,
    PageStore,
    ScraperConfig,
)
from tests.canned import (
    CHALLENGE,
    CannedTransport,
    FakeBrowserPage,
    fake_playwright_module,
)
from tests.ddo_sync.conftest import (
    ITEM_PAGE_HTML,
    UPDATE_PAGE_HTML,
    InMemoryItemWriter,
    InMemoryPageStore,
    serve_wiki,
    utc,
    wiki_page,
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
def store() -> InMemoryPageStore:
    return InMemoryPageStore(serve_wiki)


@pytest.fixture
def writer() -> InMemoryItemWriter:
    return InMemoryItemWriter()


@pytest.fixture
def syncer(store, writer, queue_repo) -> DDOSyncer:
    return DDOSyncer(store, writer, queue_repo)


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

    def test_marks_page_synced_with_the_revision_read(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        status = queue_repo.get_update_page_status(PAGE_NAME)
        assert status.last_synced_at is not None
        assert status.revision_id == 628001

    def test_refreshed_page_records_its_new_revision(self, writer, queue_repo, store):
        DDOSyncer(store, writer, queue_repo).sync_update_page(PAGE_NAME)
        store.serve = lambda _url: UPDATE_PAGE_HTML.replace(
            '"wgCurRevisionId":628001', '"wgCurRevisionId":628002'
        )
        DDOSyncer(store, writer, queue_repo).sync_update_page(PAGE_NAME)
        assert queue_repo.get_update_page_status(PAGE_NAME).revision_id == 628001

        DDOSyncer(store, writer, queue_repo, refresh=True).sync_update_page(PAGE_NAME)
        assert queue_repo.get_update_page_status(PAGE_NAME).revision_id == 628002

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

    def test_refresh_refetches_held_pages(self, store, writer, queue_repo):
        syncer = DDOSyncer(store, writer, queue_repo, refresh=True)
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
        assert item.wiki.title == "Item:" + item.name
        assert item.wiki.url.startswith("https://ddowiki.com/page/Item:")
        assert report["template"] == "shield"
        assert report["update_page"] == PAGE_NAME

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

    def test_nameless_set_warning_reaches_the_report_line(
        self, store, queue_repo, tmp_path
    ):
        nameless_set = wiki_page(
            "Item:Loose_Piece",
            '<table class="wikitable"><tr><th>Minimum Level</th><td>5</td></tr>'
            "<tr><th>Enchantments</th><td><ul>"
            "<li>2 Pieces Equipped: +3 Artifact bonus to Strength</li>"
            "</ul></td></tr></table>",
        )
        store.serve = lambda url: (
            nameless_set if "/page/Item:" in url else serve_wiki(url)
        )
        registry = Registry.load(tmp_path / "registry.jsonl")
        writer = CatalogWriter(registry, tmp_path / "items", tmp_path)
        syncer = DDOSyncer(store, writer, queue_repo)
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        syncer.process_queue(limit=1)

        report_path = tmp_path / "update-5" / "report.jsonl"
        [line] = [json.loads(raw) for raw in report_path.read_text().splitlines()]
        assert line["warnings"] == ["named set with 1 bonus(es) has no name"]

    def test_a_page_missing_behind_a_challenge_fails_its_item(
        self, queue_repo, writer, sword_link, tmp_path, monkeypatch
    ):
        page = FakeBrowserPage()
        page.documents[sword_link.wiki_url] = [
            (202, "<html>challenge</html>"),
            (404, '<html><div id="mw-content-text">no text</div></html>'),
        ]
        monkeypatch.setitem(
            sys.modules, "playwright.sync_api", fake_playwright_module(page)
        )
        config = ScraperConfig(
            user_agent="ddoloot-test",
            cache_dir=tmp_path / "pages",
            respect_robots_txt=False,
            browser=BrowserPolicy(enabled=True),
        )
        plain = CannedTransport(default=lambda _url: CHALLENGE)
        store = PageStore(config, transport=plain, sleep=lambda _s: None)
        queue_repo.register_update_page(PAGE_NAME, PAGE_URL)
        queue_repo.enqueue_items([sword_link])

        success, failures = DDOSyncer(store, writer, queue_repo).process_queue()

        assert (success, failures) == (0, 1)
        assert writer.written == []
        assert list(store.iter_cached()) == []
        [item] = queue_repo.get_items_for_update_page(sword_link.update_page)
        assert item.status == "failed"
        assert "page not found (404)" in item.error_message


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

    def test_reads_every_registered_update_page_and_processes_the_queue(
        self, syncer, store, writer
    ):
        syncer.register_update_page(PAGE_NAME)
        status = syncer.sync_all()
        assert store.requests[0] == PAGE_URL
        assert status.queue_stats.complete == 3
        assert status.update_pages[PAGE_NAME].revision_id == 628001
        assert len(writer.written) == 3

    def test_second_cycle_makes_no_request(self, syncer, store):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_all()
        store.requests.clear()
        syncer.sync_all()
        assert store.requests == []

    def test_limit_caps_processed_items(self, syncer):
        syncer.register_update_page(PAGE_NAME)
        status = syncer.sync_all(limit=1)
        assert status.queue_stats.complete == 1
        assert status.queue_stats.pending == 2

    def test_resets_failed_items_first(self, syncer, queue_repo):
        syncer.register_update_page(PAGE_NAME)
        syncer.sync_update_page(PAGE_NAME)
        items = queue_repo.get_pending_items()
        queue_repo.mark_failed(items[0].id, utc(2025, 11, 1), "err")
        assert queue_repo.get_queue_stats().failed == 1
        syncer.sync_all()
        assert queue_repo.get_queue_stats().failed == 0

    def test_update_page_error_does_not_abort_cycle(self, syncer, store):
        """If one update page fails to sync, others still process."""
        syncer.register_update_page("Page_A")
        syncer.register_update_page("Page_B")

        def fetch_side_effect(url):
            if "Page_A" in url:
                raise FetchError("Page A broken", url=url)
            return serve_wiki(url)

        store.serve = fetch_side_effect
        # Should not raise even though Page_A fails
        result = syncer.sync_all()
        assert result.update_pages["Page_A"].last_synced_at is None
        assert result.update_pages["Page_B"].last_synced_at is not None
        assert result.queue_stats.complete == 3

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
