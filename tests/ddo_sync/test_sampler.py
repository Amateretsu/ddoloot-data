"""sample_pages(): stratified sampling of queued item pages, with in-memory fakes."""

from __future__ import annotations

import pytest

from ddo_sync import ItemLink, QueueRepository, sample_pages
from page_store import ChallengeError, FetchError
from tests.ddo_sync.conftest import InMemoryPageStore, serve_wiki

UPDATES = {f"Update_{u}_named_items": 4 for u in (5, 6, 7)}


@pytest.fixture
def queue_repo() -> QueueRepository:
    repo = QueueRepository(":memory:")
    repo.open()
    for update, count in UPDATES.items():
        repo.register_update_page(update, f"https://ddowiki.com/page/{update}")
        repo.enqueue_items(
            [
                ItemLink(
                    item_name=f"{update} item {n}",
                    wiki_url=f"https://ddowiki.com/page/Item:{update}_item_{n}",
                    update_page=update,
                )
                for n in range(count)
            ]
        )
    yield repo
    repo.close()


@pytest.fixture
def store() -> InMemoryPageStore:
    return InMemoryPageStore(serve_wiki)


def test_sample_spans_as_many_update_pages_as_possible(queue_repo, store):
    results = sample_pages(queue_repo, store, count=3)

    assert len(results) == 3
    assert {r.item.update_page for r in results} == set(UPDATES)
    assert [r.page.url for r in results] == [r.item.wiki_url for r in results]


def test_sample_is_repeatable_for_a_seed(queue_repo, store):
    first = [r.item.wiki_url for r in sample_pages(queue_repo, store, count=5, seed=7)]
    again = [r.item.wiki_url for r in sample_pages(queue_repo, store, count=5, seed=7)]
    assert first == again


def test_sample_never_exceeds_the_queue(queue_repo, store):
    results = sample_pages(queue_repo, store, count=100)
    assert len(results) == sum(UPDATES.values())
    assert len({r.item.wiki_url for r in results}) == len(results)


def test_sample_counts_items_whatever_their_status(queue_repo, store):
    for item in queue_repo.get_pending_items():
        queue_repo.mark_complete(item.id, item.queued_at)
    assert len(sample_pages(queue_repo, store, count=2)) == 2


def test_pages_already_held_cost_no_request(queue_repo, store):
    sample_pages(queue_repo, store, count=3)
    store.requests.clear()

    results = sample_pages(queue_repo, store, count=3)

    assert store.requests == []
    assert all(r.page is not None for r in results)


def test_a_page_that_cannot_be_fetched_is_reported_and_the_sample_goes_on(
    queue_repo, store
):
    def serve(url: str) -> str:
        if url.endswith("_0"):
            raise FetchError("page not found (404)", url=url, status=404)
        return serve_wiki(url)

    store.serve = serve
    results = sample_pages(queue_repo, store, count=100)

    failed = [r for r in results if r.error]
    assert len(failed) == len(UPDATES)
    assert all(r.page is None and "404" in r.error for r in failed)
    assert len(results) == sum(UPDATES.values())


def test_a_stopped_run_propagates(queue_repo, store):
    def serve(url: str) -> str:
        raise ChallengeError("challenged", url=url)

    store.serve = serve
    with pytest.raises(ChallengeError):
        sample_pages(queue_repo, store, count=3)


def test_an_empty_queue_samples_nothing(store):
    with QueueRepository(":memory:") as repo:
        assert sample_pages(repo, store, count=5) == []
    assert store.requests == []
