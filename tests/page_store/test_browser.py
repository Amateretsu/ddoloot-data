"""The browser adapter, with a fake Playwright: no browser is launched, no wiki reached.

Through the Page Store where the behaviour is the store's (what is held, what fails), and
through :class:`BrowserTransport` directly for the status it reports.
"""

from __future__ import annotations

import sys

import pytest

from page_store import BrowserPolicy, FetchError, PageStore
from page_store.browser import BrowserTransport
from tests.canned import (
    CHALLENGE,
    CannedTransport,
    FakeBrowserPage,
    fake_playwright_module,
)
from tests.page_store.test_page_store import BOW, Sleeps, make_config

CHALLENGE_PAGE = "<html><body><script src='challenge.js'></script></body></html>"
ARTICLE = "<html><div id='mw-content-text'>Legendary Gnollish War Bow</div></html>"
NO_ARTICLE = "<html><div id='mw-content-text'>There is currently no text.</div></html>"


@pytest.fixture
def page(monkeypatch) -> FakeBrowserPage:
    page = FakeBrowserPage()
    monkeypatch.setitem(
        sys.modules, "playwright.sync_api", fake_playwright_module(page)
    )
    return page


@pytest.fixture
def challenged_store(tmp_path) -> PageStore:
    """Every plain fetch is challenged; the browser is the real adapter, on the fake."""
    plain = CannedTransport(default=lambda _url: CHALLENGE)
    config = make_config(tmp_path, browser=BrowserPolicy(enabled=True))
    return PageStore(config, transport=plain, sleep=Sleeps())


def test_a_page_missing_after_the_challenge_is_not_stored(challenged_store, page):
    page.documents[BOW] = [(202, CHALLENGE_PAGE), (404, NO_ARTICLE)]

    with pytest.raises(FetchError, match="page not found") as exc:
        challenged_store.get(BOW)

    assert exc.value.status == 404
    assert page.visited == [BOW]  # a missing page is not retried
    assert list(challenged_store.iter_cached()) == []


def test_a_page_found_after_the_challenge_is_stored(challenged_store, page):
    page.documents[BOW] = [(202, CHALLENGE_PAGE), (200, ARTICLE)]

    fetched = challenged_store.get(BOW)

    assert fetched.via == "browser"
    assert fetched.html == ARTICLE
    assert list(challenged_store.iter_cached()) == [fetched]


@pytest.mark.parametrize(
    ("documents", "status"),
    [
        ([(200, ARTICLE)], 200),
        ([(404, NO_ARTICLE)], 404),
        ([(202, CHALLENGE_PAGE), (200, ARTICLE)], 200),
        ([(202, CHALLENGE_PAGE), (404, NO_ARTICLE)], 404),
        ([(202, CHALLENGE_PAGE), (503, ARTICLE)], 503),
        ([(202, CHALLENGE_PAGE)], 202),  # the challenge never cleared
    ],
)
def test_the_status_is_the_final_documents(page, documents, status):
    page.documents[BOW] = documents
    transport = BrowserTransport("ddoloot-test", timeout_seconds=1)

    resp = transport.fetch(BOW)

    assert resp.status == status
    assert resp.text == documents[-1][1]
