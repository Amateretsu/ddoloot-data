"""The browser adapter, with a fake Playwright: no browser is launched, no wiki reached.

Through the Page Store where the behaviour is the store's (what is held, what fails), and
through :class:`BrowserTransport` directly for the status it reports.
"""

from __future__ import annotations

import sys
import types

import pytest
from loguru import logger

from page_store import BrowserPolicy, FetchError, PageStore
from page_store.browser import MAX_WIKI_REQUESTS, BrowserTransport
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


# ── Diagnostics: what a --verbose log shows when the article never appears ──────


@pytest.fixture
def log_lines():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="DEBUG")
    yield lines
    logger.remove(sink)


@pytest.mark.parametrize(
    ("documents", "last"),
    [
        ([(202, CHALLENGE_PAGE)], "202, x-amzn-waf-action: challenge"),
        ([(405, CHALLENGE_PAGE)], "405"),  # a CAPTCHA the browser cannot clear
        ([(403, "<html>blocked</html>")], "403"),
    ],
)
def test_a_load_with_no_article_logs_the_last_document(
    page, log_lines, documents, last
):
    page.documents[BOW] = documents
    transport = BrowserTransport("ddoloot-test", timeout_seconds=1)

    assert transport.fetch(BOW).status == 202  # the store's one challenge check

    assert f"document {BOW} -> {last} (browser)" in log_lines
    (summary,) = [m for m in log_lines if m.startswith("no article for")]
    assert f"no article for {BOW} after 1s: 1 document(s) seen" in summary
    assert f"last document {last};" in summary
    assert "browser up" in summary


def test_a_cleared_challenge_logs_both_documents_and_no_summary(page, log_lines):
    page.documents[BOW] = [(202, CHALLENGE_PAGE), (200, ARTICLE)]

    BrowserTransport("ddoloot-test", timeout_seconds=1).fetch(BOW)

    assert [m for m in log_lines if m.startswith("document ")] == [
        f"document {BOW} -> 202, x-amzn-waf-action: challenge (browser)",
        f"document {BOW} -> 200 (browser)",
    ]
    assert not [m for m in log_lines if m.startswith("no article")]


class _Route:
    def __init__(self, url: str, resource_type: str) -> None:
        self.request = types.SimpleNamespace(url=url, resource_type=resource_type)
        self.outcome = ""

    def continue_(self) -> None:
        self.outcome = "continued"

    def abort(self) -> None:
        self.outcome = "aborted"


def test_routing_logs_passed_hosts_and_the_blocked_extra_document(log_lines):
    transport = BrowserTransport("ddoloot-test", timeout_seconds=1)
    token = _Route("https://abc.token.awswaf.com/abc/verify?secret=1", "fetch")
    pages = [_Route(BOW, "document") for _ in range(MAX_WIKI_REQUESTS + 1)]

    for route in [token, *pages]:
        transport._route(route)

    assert token.outcome == "continued"
    assert [r.outcome for r in pages] == ["continued"] * MAX_WIKI_REQUESTS + ["aborted"]
    assert log_lines == [
        "pass fetch https://abc.token.awswaf.com/abc/verify (browser)",
        *[f"GET {BOW} (browser)"] * MAX_WIKI_REQUESTS,
        f"blocked document {BOW} (browser)",
    ]
