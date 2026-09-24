"""The browser adapter, with a fake Playwright: no browser is launched, no wiki reached.

Through the Page Store where the behaviour is the store's (what is held, what fails), and
through :class:`BrowserTransport` directly for the status it reports.
"""

from __future__ import annotations

import sys
import types

import pytest
from loguru import logger

from page_store import BrowserPolicy, FetchError, PageStore, Response, RunStoppedError
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
UNAVAILABLE = "<html><body>503 Service Unavailable</body></html>"
BLOCKED = "<html><body>403 Forbidden</body></html>"


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
        ([(202, CHALLENGE_PAGE), (503, UNAVAILABLE)], 503),  # the reload failed
        ([(202, CHALLENGE_PAGE), (403, BLOCKED)], 403),
        ([(202, CHALLENGE_PAGE)], 202),  # the challenge never cleared
    ],
)
def test_the_status_is_the_final_documents(page, documents, status):
    page.documents[BOW] = documents
    transport = BrowserTransport("ddoloot-test", timeout_seconds=1)

    resp = transport.fetch(BOW)

    assert resp.status == status
    assert resp.text == documents[-1][1]


@pytest.mark.parametrize("status", [403, 404, 405, 429, 500, 503])
def test_a_first_document_that_is_no_challenge_returns_at_once(page, status):
    page.documents[BOW] = [(status, UNAVAILABLE)]
    transport = BrowserTransport("ddoloot-test", timeout_seconds=1)

    resp = transport.fetch(BOW)

    assert resp.status == status
    assert resp.text == UNAVAILABLE
    assert resp.header("x-amzn-waf-action") is None  # not disguised as a challenge
    assert page.waits == 0  # no 30 s wait for an article that cannot come


def test_the_first_documents_headers_are_passed_on(page):
    page.documents[BOW] = [(429, UNAVAILABLE, {"retry-after": "120"})]

    resp = BrowserTransport("ddoloot-test", timeout_seconds=1).fetch(BOW)

    assert resp.status == 429
    assert resp.header("Retry-After") == "120"


def test_a_challenge_is_waited_for(page):
    page.documents[BOW] = [(202, CHALLENGE_PAGE), (200, ARTICLE)]

    BrowserTransport("ddoloot-test", timeout_seconds=1).fetch(BOW)

    assert page.waits == 1


def test_a_reload_with_no_article_still_reports_the_challenge(page):
    """A 200 reload whose article never appeared is not stored as a success."""
    page.documents[BOW] = [(202, CHALLENGE_PAGE), (200, CHALLENGE_PAGE)]

    resp = BrowserTransport("ddoloot-test", timeout_seconds=1).fetch(BOW)

    assert resp.status == 202
    assert resp.header("x-amzn-waf-action") == "challenge"


def test_a_browser_503_is_retried_and_fails_the_page_without_stopping_the_run(
    tmp_path, page
):
    """A 503 in the browser is a 5xx to the Page Store, not an uncleared challenge."""
    page.documents[BOW] = [(503, UNAVAILABLE)]
    plain = CannedTransport(default=lambda _url: CHALLENGE)
    config = make_config(tmp_path, max_retries=2, browser=BrowserPolicy(enabled=True))
    store = PageStore(config, transport=plain, sleep=Sleeps())

    with pytest.raises(FetchError, match=r"gave up .* after 3 attempt\(s\): HTTP 503"):
        store.get(BOW)

    assert page.visited == [BOW] * 3  # 1 + max_retries
    assert list(store.iter_cached()) == []


def test_a_fake_browser_transports_503_is_a_5xx_not_a_challenge(tmp_path):
    """Through the Page Store with a canned browser transport: retried, failed, held nothing."""
    plain = CannedTransport(default=lambda _url: CHALLENGE)
    browser = CannedTransport(default=lambda _url: Response(status=503))
    config = make_config(tmp_path, max_retries=2, browser=BrowserPolicy(enabled=True))
    store = PageStore(config, transport=plain, browser=browser, sleep=Sleeps())

    with pytest.raises(FetchError) as exc:
        store.get(BOW)

    assert not isinstance(exc.value, RunStoppedError)
    assert exc.value.status == 503
    assert browser.requests == [BOW] * 3
    assert list(store.iter_cached()) == []


# ── Diagnostics: what a --verbose log shows when the article never appears ──────


@pytest.fixture
def log_lines():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="DEBUG")
    yield lines
    logger.remove(sink)


@pytest.mark.parametrize(
    ("documents", "last", "status"),
    [
        ([(202, CHALLENGE_PAGE)], "202, x-amzn-waf-action: challenge", 202),
        ([(202, CHALLENGE_PAGE), (405, CHALLENGE_PAGE)], "405", 405),  # a CAPTCHA
        ([(202, CHALLENGE_PAGE), (403, BLOCKED)], "403", 403),
    ],
)
def test_a_load_with_no_article_logs_the_last_document(
    page, log_lines, documents, last, status
):
    page.documents[BOW] = documents
    transport = BrowserTransport("ddoloot-test", timeout_seconds=1)

    assert transport.fetch(BOW).status == status

    assert f"document {BOW} -> {last} (browser)" in log_lines
    (summary,) = [m for m in log_lines if m.startswith("no article for")]
    n = len(documents)
    assert f"no article for {BOW} after 1s: {n} document(s) seen" in summary
    assert f"last document {last};" in summary
    assert "browser up" in summary


def test_a_load_that_did_not_wait_logs_no_summary(page, log_lines):
    page.documents[BOW] = [(503, UNAVAILABLE)]

    BrowserTransport("ddoloot-test", timeout_seconds=1).fetch(BOW)

    assert f"document {BOW} -> 503 (browser)" in log_lines
    assert not [m for m in log_lines if m.startswith("no article")]


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
