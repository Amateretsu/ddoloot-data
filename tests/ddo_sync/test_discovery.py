"""The discovery module, through its public functions and the in-memory Page Store fake.

Pages are served in the real wiki skin (see ``wiki_page``), so the skin's own links are in
every page, as they are on the live wiki.
"""

from __future__ import annotations

import pytest

from ddo_sync import (
    NAMED_ITEMS_INDEX_URL,
    ItemLink,
    UpdatePageError,
    discover_update_pages,
    read_update_page,
    update_page_url,
    update_slug,
)
from page_store import ChallengeError, FetchError
from tests.ddo_sync.conftest import (
    INDEX_URL,
    ITEM_PAGE_HTML,
    InMemoryPageStore,
    serve_wiki,
    wiki_page,
)

UPDATE_5_URL = "https://ddowiki.com/page/Update_5_named_items"


def _raise(exc: Exception):
    def serve(_url: str) -> str:
        raise exc

    return serve


@pytest.fixture
def store() -> InMemoryPageStore:
    return InMemoryPageStore(serve_wiki)


# ── discover_update_pages ─────────────────────────────────────────────────────


def test_index_url_is_a_page_read():
    assert NAMED_ITEMS_INDEX_URL == INDEX_URL


def test_discovers_update_pages_from_the_index_in_update_order(store):
    assert discover_update_pages(store) == [
        "Update_5_named_items",
        "Update_8_named_items",
        "Update_10_named_items",
    ]
    assert store.requests == [INDEX_URL]


def test_held_index_is_not_refetched_unless_refresh(store):
    discover_update_pages(store)
    discover_update_pages(store)
    assert store.requests == [INDEX_URL]

    discover_update_pages(store, refresh=True)
    assert store.requests == [INDEX_URL, INDEX_URL]


def test_index_without_update_links_is_an_error():
    store = InMemoryPageStore(lambda _url: ITEM_PAGE_HTML)
    with pytest.raises(UpdatePageError) as exc:
        discover_update_pages(store)
    assert exc.value.page_url == INDEX_URL


def test_index_fetch_error_is_an_update_page_error():
    store = InMemoryPageStore(_raise(FetchError("gone", url=INDEX_URL, status=404)))
    with pytest.raises(UpdatePageError):
        discover_update_pages(store)


def test_challenge_on_the_index_stops_the_run():
    store = InMemoryPageStore(_raise(ChallengeError("challenged", url=INDEX_URL)))
    with pytest.raises(ChallengeError):
        discover_update_pages(store)


# ── read_update_page ──────────────────────────────────────────────────────────


def test_reads_item_links_in_document_order_without_duplicates(store):
    page = read_update_page(store, "Update_5_named_items")

    assert page.links == [
        ItemLink(
            "Sword of Shadow",
            "https://ddowiki.com/page/Item:Sword_of_Shadow",
            "Update_5_named_items",
        ),
        ItemLink(
            "Shield of Light",
            "https://ddowiki.com/page/Item:Shield_of_Light",
            "Update_5_named_items",
        ),
        ItemLink(
            "Ring of Fire",
            "https://ddowiki.com/page/Item:Ring_of_Fire",
            "Update_5_named_items",
        ),
    ]
    assert page.page_name == "Update_5_named_items"
    assert page.url == UPDATE_5_URL
    assert store.requests == [UPDATE_5_URL]


def test_revision_id_comes_from_the_page_html(store):
    assert read_update_page(store, "Update_5_named_items").revision_id == 628001


def test_page_without_a_revision_id_reads_as_none():
    html = wiki_page(
        "Update_7_named_items", '<a href="/page/Item:Foo">Foo</a>', revision_id=None
    )
    page = read_update_page(
        InMemoryPageStore(lambda _url: html), "Update_7_named_items"
    )
    assert page.revision_id is None
    assert [link.item_name for link in page.links] == ["Foo"]


def test_spaces_in_the_page_name_are_accepted(store):
    page = read_update_page(store, "Update 5 named items")
    assert page.page_name == "Update_5_named_items"
    assert store.requests == [UPDATE_5_URL]


def test_percent_encoded_names_are_decoded_and_urls_kept_as_the_wiki_wrote_them():
    html = wiki_page(
        "Update_7_named_items",
        '<a href="/page/Item:Ghal%27s_Ring">Ghal\'s Ring</a>'
        '<a href="https://ddowiki.com/page/Item:Absolute_Blade">Absolute Blade</a>',
    )
    links = read_update_page(
        InMemoryPageStore(lambda _url: html), "Update_7_named_items"
    ).links
    assert [(link.item_name, link.wiki_url) for link in links] == [
        ("Ghal's Ring", "https://ddowiki.com/page/Item:Ghal%27s_Ring"),
        ("Absolute Blade", "https://ddowiki.com/page/Item:Absolute_Blade"),
    ]


def test_held_update_page_is_not_refetched_unless_refresh(store):
    read_update_page(store, "Update_5_named_items")
    read_update_page(store, "Update_5_named_items")
    read_update_page(store, "Update_5_named_items", refresh=True)
    assert store.requests == [UPDATE_5_URL, UPDATE_5_URL]


def test_empty_update_page_is_an_error():
    with pytest.raises(UpdatePageError):
        read_update_page(InMemoryPageStore(lambda _url: "  \n"), "Update_5_named_items")


def test_update_page_fetch_error_is_an_update_page_error():
    store = InMemoryPageStore(_raise(FetchError("down", url=UPDATE_5_URL)))
    with pytest.raises(UpdatePageError) as exc:
        read_update_page(store, "Update_5_named_items")
    assert exc.value.page_url == UPDATE_5_URL


def test_challenge_on_an_update_page_stops_the_run():
    store = InMemoryPageStore(_raise(ChallengeError("challenged", url=UPDATE_5_URL)))
    with pytest.raises(ChallengeError):
        read_update_page(store, "Update_5_named_items")


# ── names ─────────────────────────────────────────────────────────────────────


def test_update_page_url():
    assert update_page_url("Update 5 named items") == UPDATE_5_URL


@pytest.mark.parametrize(
    ("page_name", "slug"),
    [
        ("Update_8_named_items", "update-8"),
        ("Update 12 named items", "update-12"),
        ("update_08_named_items", "update-8"),
        ("Update_50_revamped_named_items", "unknown"),
        ("Named_items", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_update_slug(page_name, slug):
    assert update_slug(page_name) == slug
