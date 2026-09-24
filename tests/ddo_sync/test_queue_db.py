"""The queue module, through QueueRepository's public API against a temporary SQLite file."""

from __future__ import annotations

import sqlite3

import pytest

from ddo_sync import ItemLink, QueueRepository, QueueSchemaError, QueueStats
from tests.ddo_sync.conftest import SYNCED_AT, utc

PAGE_NAME = "Update_5_named_items"
PAGE_URL = "https://ddowiki.com/page/Update_5_named_items"


def _register(repo: QueueRepository, name: str = PAGE_NAME) -> None:
    repo.register_update_page(name, f"https://ddowiki.com/page/{name}")


def _pending_one(repo: QueueRepository, link: ItemLink):
    _register(repo)
    repo.enqueue_items([link])
    return repo.get_pending_items()[0]


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def test_state_survives_reopening_the_file(tmp_path, sword_link):
    db = str(tmp_path / "queue.db")
    with QueueRepository(db) as repo:
        _register(repo)
        repo.enqueue_items([sword_link])
        repo.mark_page_synced(PAGE_NAME, SYNCED_AT, revision_id=628001)
    with QueueRepository(db) as repo:
        status = repo.get_update_page_status(PAGE_NAME)
        assert repo.get_queue_stats().pending == 1
    assert status.last_synced_at == SYNCED_AT
    assert status.revision_id == 628001


def test_open_and_close_are_idempotent_and_first_use_opens(tmp_path):
    repo = QueueRepository(str(tmp_path / "queue.db"))
    _register(repo)
    repo.open()
    repo.close()
    repo.close()


def test_a_file_from_an_older_schema_is_refused(tmp_path):
    db = tmp_path / "queue.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE update_pages (page_name TEXT PRIMARY KEY, page_url TEXT NOT NULL,"
        " last_synced_at TEXT, wiki_modified_at TEXT)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(QueueSchemaError, match="delete the file"):
        QueueRepository(str(db)).open()


def test_an_unopenable_path_is_a_schema_error(tmp_path):
    with pytest.raises(QueueSchemaError):
        QueueRepository(str(tmp_path / "missing" / "queue.db")).open()


# ── Update pages ──────────────────────────────────────────────────────────────


def test_registered_page_starts_unsynced(queue_repo):
    _register(queue_repo)
    _register(queue_repo)
    [status] = queue_repo.list_update_pages()
    assert status.page_name == PAGE_NAME
    assert status.page_url == PAGE_URL
    assert status.last_synced_at is None
    assert status.revision_id is None


def test_mark_page_synced_records_time_and_revision(queue_repo):
    _register(queue_repo)
    queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT, revision_id=1)
    queue_repo.mark_page_synced(PAGE_NAME, utc(2025, 11, 2), revision_id=2)
    status = queue_repo.get_update_page_status(PAGE_NAME)
    assert status.last_synced_at == utc(2025, 11, 2)
    assert status.revision_id == 2


def test_mark_page_synced_without_a_revision(queue_repo):
    _register(queue_repo)
    queue_repo.mark_page_synced(PAGE_NAME, SYNCED_AT)
    assert queue_repo.get_update_page_status(PAGE_NAME).revision_id is None


def test_unknown_page_has_no_status(queue_repo):
    assert queue_repo.get_update_page_status("Nope") is None


def test_update_pages_are_listed_by_name(queue_repo):
    _register(queue_repo, "Zebra_Page")
    _register(queue_repo, "Alpha_Page")
    assert [p.page_name for p in queue_repo.list_update_pages()] == [
        "Alpha_Page",
        "Zebra_Page",
    ]


# ── Queue ─────────────────────────────────────────────────────────────────────


def test_enqueue_ignores_items_already_queued_for_the_page(queue_repo, item_links):
    _register(queue_repo)
    assert queue_repo.enqueue_items(item_links) == 2
    assert queue_repo.enqueue_items(item_links) == 0
    assert queue_repo.enqueue_items([]) == 0
    items = queue_repo.get_pending_items()
    assert {i.item_name for i in items} == {"Sword of Shadow", "Shield of Light"}
    assert all(i.status == "pending" and i.retry_count == 0 for i in items)


def test_same_item_on_two_update_pages_is_queued_twice(queue_repo, sword_link):
    _register(queue_repo, "Page_A")
    _register(queue_repo, "Page_B")
    for page in ("Page_A", "Page_B"):
        link = ItemLink(sword_link.item_name, sword_link.wiki_url, page)
        assert queue_repo.enqueue_items([link]) == 1
    assert len(queue_repo.get_items_for_update_page("Page_A")) == 1
    assert queue_repo.get_items_for_update_page("Nope") == []


def test_status_transitions_are_counted(queue_repo, item_links):
    _register(queue_repo)
    queue_repo.enqueue_items(item_links)
    first, second = queue_repo.get_pending_items()

    queue_repo.mark_in_progress(first.id, utc(2025, 11, 1, 10))
    assert queue_repo.get_queue_stats() == QueueStats(1, 1, 0, 0, 0)

    queue_repo.mark_complete(first.id, utc(2025, 11, 1, 11))
    queue_repo.mark_skipped(second.id)
    stats = queue_repo.get_queue_stats()
    assert stats == QueueStats(0, 0, 1, 0, 1)
    assert stats.total == 2


def test_failure_is_recorded_and_retried_below_the_cap(queue_repo, sword_link):
    item = _pending_one(queue_repo, sword_link)
    queue_repo.mark_failed(item.id, utc(2025, 11, 1), "err1")
    [failed] = queue_repo.get_items_for_update_page(PAGE_NAME)
    assert failed.status == "failed"
    assert failed.error_message == "err1"
    assert failed.retry_count == 1

    assert queue_repo.reset_failed_to_pending(max_retries=3) == 1
    queue_repo.mark_failed(item.id, utc(2025, 11, 2), "err2")
    assert queue_repo.reset_failed_to_pending(max_retries=2) == 0
    assert queue_repo.get_items_for_update_page(PAGE_NAME)[0].retry_count == 2
    assert queue_repo.get_queue_stats().failed == 1


def test_pending_items_respect_the_limit(queue_repo, item_links):
    _register(queue_repo)
    queue_repo.enqueue_items(item_links)
    assert len(queue_repo.get_pending_items(limit=1)) == 1
    assert len(queue_repo.get_pending_items()) == 2
