"""The Page Store, through get() and iter_cached(), with a canned transport and a tmp cache.

No test here reaches the wiki: every request goes to a CannedTransport, and the crawl
delay is recorded instead of slept.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from page_store import (
    RATIO_MIN_SAMPLE,
    BrowserPolicy,
    ChallengeError,
    FetchError,
    PageStore,
    Response,
    RunStoppedError,
    ScraperConfig,
    ScraperConfigError,
    TransportError,
    load_scraper_config,
)
from tests.canned import CHALLENGE, CannedTransport, ok

WIKI = "https://ddowiki.com/page/"
BOW = WIKI + "Item:Legendary_Gnollish_War_Bow"
VEST = WIKI + "Item:Dark_Ressurectionist%27s_Frock_Vest"
ROBOTS = "https://ddowiki.com/robots.txt"


def item_url(n: int) -> str:
    return f"{WIKI}Item:Page_{n}"


def page_html(url: str) -> Response:
    return ok(f"<html><body>{url}</body></html>")


def make_config(tmp_path: Path, **overrides) -> ScraperConfig:
    values = {
        "user_agent": "ddoloot-test",
        "cache_dir": tmp_path / "pages",
        "respect_robots_txt": False,
        **overrides,
    }
    return ScraperConfig(**values)


class Sleeps(list):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def plain() -> CannedTransport:
    return CannedTransport(default=page_html)


@pytest.fixture
def browser() -> CannedTransport:
    return CannedTransport(default=lambda url: ok(f"<html>browser {url}</html>"))


@pytest.fixture
def store(tmp_path, plain, sleeps) -> PageStore:
    return PageStore(make_config(tmp_path), transport=plain, sleep=sleeps)


def browser_store(tmp_path, plain, browser, sleeps, **policy) -> PageStore:
    config = make_config(tmp_path, browser=BrowserPolicy(enabled=True, **policy))
    return PageStore(config, transport=plain, browser=browser, sleep=sleeps)


# ── get() and the cache ──────────────────────────────────────────────────────


def test_get_fetches_a_page_it_does_not_hold(store, plain):
    page = store.get(BOW)

    assert plain.requests == [BOW]
    assert page.html == f"<html><body>{BOW}</body></html>"
    assert page.url == BOW
    assert page.title == "Item:Legendary Gnollish War Bow"
    assert page.via == "plain"
    assert page.fetched_at.tzinfo is not None


def test_a_held_page_costs_no_request(store, plain):
    first = store.get(BOW)
    again = store.get(BOW)

    assert plain.requests == [BOW]
    assert again == first


def test_the_store_survives_a_new_run(tmp_path, plain, sleeps):
    PageStore(make_config(tmp_path), transport=plain, sleep=sleeps).get(BOW)
    later = CannedTransport()

    page = PageStore(make_config(tmp_path), transport=later, sleep=sleeps).get(BOW)

    assert later.requests == []
    assert page.html == f"<html><body>{BOW}</body></html>"


def test_refresh_refetches_a_held_page(store, plain):
    store.get(BOW)
    plain.reply(BOW, ok("<html>new revision</html>"))

    page = store.get(BOW, refresh=True)

    assert plain.requests == [BOW, BOW]
    assert page.html == "<html>new revision</html>"
    assert store.get(BOW).html == "<html>new revision</html>"


def test_urls_naming_the_same_title_share_one_copy(store, plain):
    store.get(VEST)
    store.get(WIKI + "Item:Dark_Ressurectionist's_Frock_Vest")
    store.get(WIKI + "Item:Dark Ressurectionist's Frock Vest")

    assert plain.requests == [VEST]


def test_titles_that_slug_alike_are_kept_apart(tmp_path, plain, sleeps):
    apostrophe, space = WIKI + "Item:A'B", WIKI + "Item:A_B"
    store = PageStore(make_config(tmp_path), transport=plain, sleep=sleeps)
    store.get(apostrophe)
    store.get(space)

    reopened = PageStore(
        make_config(tmp_path), transport=CannedTransport(), sleep=sleeps
    )
    assert reopened.get(apostrophe).html == f"<html><body>{apostrophe}</body></html>"
    assert reopened.get(space).html == f"<html><body>{space}</body></html>"
    assert len(list(reopened.iter_cached())) == 2


def test_cache_files_have_a_readable_name(tmp_path, store):
    store.get(BOW)
    names = [p.name for p in (tmp_path / "pages").glob("*.html")]
    assert len(names) == 1
    assert names[0].startswith("Item_Legendary_Gnollish_War_Bow-")


@pytest.mark.parametrize(
    "url",
    [
        "https://ddowiki.com/api.php?action=query&titles=Item:Sword",
        "https://ddowiki.com/page/Special:AllPages",
        "https://ddowiki.com/page/Item:Sword?action=raw",
        "https://example.com/page/Item:Sword",
        "https://ddowiki.com/index.php?title=Item:Sword",
    ],
)
def test_only_wiki_article_pages_may_be_read(store, plain, url):
    with pytest.raises(ValueError):
        store.get(url)
    assert plain.requests == []


def test_iter_cached_is_empty_for_a_new_store(store):
    assert list(store.iter_cached()) == []


def test_iter_cached_yields_every_held_page_by_title_without_requests(store, plain):
    store.get(VEST)
    store.get(BOW)
    plain.requests.clear()

    titles = [page.title for page in store.iter_cached()]

    assert titles == [
        "Item:Dark Ressurectionist's Frock Vest",
        "Item:Legendary Gnollish War Bow",
    ]
    assert plain.requests == []


def test_close_closes_the_transports(tmp_path, plain, browser, sleeps):
    with browser_store(tmp_path, plain, browser, sleeps) as store:
        store.get(BOW)
    assert plain.closed
    assert browser.closed


# ── Pacing ───────────────────────────────────────────────────────────────────


def test_requests_are_at_least_four_seconds_apart(store, sleeps):
    store.get(item_url(1))
    store.get(item_url(2))
    store.get(item_url(3))

    assert len(sleeps) == 2
    assert all(3.5 < s <= 4.0 for s in sleeps)


def test_held_pages_are_not_paced(store, sleeps):
    store.get(BOW)
    store.get(BOW)
    list(store.iter_cached())
    assert sleeps == []


def test_a_longer_configured_delay_is_used(tmp_path, plain, sleeps):
    store = PageStore(
        make_config(tmp_path, crawl_delay_seconds=7), transport=plain, sleep=sleeps
    )
    store.get(item_url(1))
    store.get(item_url(2))
    assert 6.5 < sleeps[0] <= 7.0


# ── robots.txt ───────────────────────────────────────────────────────────────


def robots_store(tmp_path, plain, sleeps, **overrides) -> PageStore:
    config = make_config(tmp_path, respect_robots_txt=True, **overrides)
    return PageStore(config, transport=plain, sleep=sleeps)


def test_robots_txt_is_read_once_through_the_transport(tmp_path, plain, sleeps):
    plain.reply(ROBOTS, ok("User-agent: *\nDisallow: /index.php\n"))
    store = robots_store(tmp_path, plain, sleeps)

    store.get(item_url(1))
    store.get(item_url(2))

    assert plain.requests == [ROBOTS, item_url(1), item_url(2)]


def test_a_page_robots_txt_disallows_is_not_fetched(tmp_path, plain, sleeps):
    plain.reply(ROBOTS, ok("User-agent: *\nDisallow: /page/Item:Secret\n"))
    store = robots_store(tmp_path, plain, sleeps)

    with pytest.raises(FetchError, match=r"robots\.txt"):
        store.get(WIKI + "Item:Secret")
    assert plain.requests == [ROBOTS]
    assert store.get(BOW).via == "plain"


WIKI_ROBOTS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "ddowiki_robots.txt"
).read_text(encoding="utf-8")


def wiki_robots_store(tmp_path, plain, sleeps) -> PageStore:
    """The committed policy's user agent against ddowiki's real robots.txt."""
    plain.reply(ROBOTS, ok(WIKI_ROBOTS))
    return robots_store(
        tmp_path, plain, sleeps, user_agent=load_scraper_config().user_agent
    )


def test_wiki_robots_txt_allows_item_pages(tmp_path, plain, sleeps):
    store = wiki_robots_store(tmp_path, plain, sleeps)

    page = store.get(BOW)

    assert plain.requests == [ROBOTS, BOW]
    assert [p.url for p in store.iter_cached()] == [page.url]


def test_wiki_robots_txt_special_pages_are_refused_without_a_request(
    tmp_path, plain, sleeps
):
    store = wiki_robots_store(tmp_path, plain, sleeps)
    with pytest.raises(ValueError):
        store.get(WIKI + "Special:RecentChanges")
    assert BOW not in plain.requests
    assert all("Special" not in url for url in plain.requests)


def test_wiki_robots_txt_crawl_delay_is_honoured(tmp_path, plain, sleeps):
    store = wiki_robots_store(tmp_path, plain, sleeps)

    store.get(item_url(1))
    store.get(item_url(2))

    assert plain.requests == [ROBOTS, item_url(1), item_url(2)]
    assert len(sleeps) == 2
    assert all(3.5 < s <= 4.0 for s in sleeps)


def test_a_query_only_disallow_does_not_block_article_pages(tmp_path, plain, sleeps):
    plain.reply(ROBOTS, ok("User-agent: *\nDisallow: /?\n"))
    store = robots_store(tmp_path, plain, sleeps)
    assert store.get(BOW).via == "plain"


def test_the_longest_matching_rule_wins(tmp_path, plain, sleeps):
    plain.reply(
        ROBOTS,
        ok(
            "User-agent: *\nDisallow: /page/Item:*_Bow$\nAllow: /page/Item:Legendary_*\n"
        ),
    )
    store = robots_store(tmp_path, plain, sleeps)

    assert store.get(BOW).via == "plain"
    with pytest.raises(FetchError, match=r"robots\.txt"):
        store.get(WIKI + "Item:Short_Bow")


def test_a_group_naming_our_product_token_replaces_the_star_group(
    tmp_path, plain, sleeps
):
    plain.reply(
        ROBOTS,
        ok("User-agent: *\nAllow: /\n\nUser-agent: DDOLoot-data\nDisallow: /page/\n"),
    )
    store = robots_store(
        tmp_path, plain, sleeps, user_agent="DDOLoot-data (+https://example.org)"
    )
    with pytest.raises(FetchError, match=r"robots\.txt"):
        store.get(BOW)


def test_a_larger_robots_crawl_delay_wins(tmp_path, plain, sleeps):
    plain.reply(ROBOTS, ok("User-agent: *\nCrawl-delay: 10\n"))
    store = robots_store(tmp_path, plain, sleeps)

    store.get(item_url(1))
    store.get(item_url(2))

    assert 9.5 < sleeps[-1] <= 10.0


def test_a_smaller_robots_crawl_delay_never_lowers_the_floor(tmp_path, plain, sleeps):
    plain.reply(ROBOTS, ok("User-agent: *\nCrawl-delay: 1\n"))
    store = robots_store(tmp_path, plain, sleeps)

    store.get(item_url(1))
    store.get(item_url(2))

    assert 3.5 < sleeps[-1] <= 4.0


def test_missing_robots_txt_allows_everything(tmp_path, plain, sleeps):
    plain.reply(ROBOTS, Response(status=404))
    store = robots_store(tmp_path, plain, sleeps, robots_fail_open=False)
    assert store.get(BOW).via == "plain"


def test_unreadable_robots_txt_fails_open_when_configured(tmp_path, plain, sleeps):
    plain.reply(ROBOTS, CHALLENGE)
    store = robots_store(tmp_path, plain, sleeps, robots_fail_open=True)
    assert store.get(BOW).via == "plain"


def test_unreadable_robots_txt_stops_the_run_when_fail_closed(tmp_path, plain, sleeps):
    plain.reply(ROBOTS, CHALLENGE)
    store = robots_store(tmp_path, plain, sleeps, robots_fail_open=False)

    with pytest.raises(RunStoppedError, match=r"robots\.txt"):
        store.get(BOW)
    assert plain.requests == [ROBOTS]


# ── Retries and errors ───────────────────────────────────────────────────────


def test_transient_failures_are_retried(store, plain, sleeps):
    plain.reply(BOW, Response(status=503), TransportError("timed out"), Response(429))

    assert store.get(BOW).via == "plain"
    assert plain.requests == [BOW] * 4
    assert sleeps == pytest.approx([8.0, 16.0, 32.0], abs=0.5)


def test_retries_give_up_after_max_retries(tmp_path, sleeps):
    plain = CannedTransport(default=lambda _url: Response(status=500))
    store = PageStore(
        make_config(tmp_path, max_retries=2), transport=plain, sleep=sleeps
    )

    with pytest.raises(FetchError) as exc:
        store.get(BOW)

    assert exc.value.status == 500
    assert plain.requests == [BOW] * 3
    assert list(store.iter_cached()) == []


def test_a_missing_page_is_not_retried_or_cached(store, plain):
    plain.reply(BOW, Response(status=404))

    with pytest.raises(FetchError) as exc:
        store.get(BOW)

    assert exc.value.status == 404
    assert plain.requests == [BOW]
    assert list(store.iter_cached()) == []


# ── WAF challenge and browser fallback ───────────────────────────────────────


def test_challenge_with_browser_disabled_stops_the_run(store, plain):
    plain.reply(BOW, CHALLENGE)

    with pytest.raises(ChallengeError, match="browser fallback is disabled"):
        store.get(BOW)
    assert list(store.iter_cached()) == []


def test_a_waf_header_alone_is_a_challenge(store, plain):
    plain.reply(BOW, Response(status=200, text="", headers={"X-Amzn-Waf-Action": "x"}))
    with pytest.raises(ChallengeError):
        store.get(BOW)


def test_a_challenged_page_is_retried_in_the_browser(tmp_path, plain, browser, sleeps):
    plain.reply(BOW, CHALLENGE)
    store = browser_store(tmp_path, plain, browser, sleeps)

    page = store.get(BOW)

    assert page.via == "browser"
    assert page.html == f"<html>browser {BOW}</html>"
    assert browser.requests == [BOW]
    assert store.get(BOW) == page
    assert len(sleeps) == 1  # the browser request is paced like any other


def test_one_challenge_does_not_switch_the_run(tmp_path, plain, browser, sleeps):
    plain.reply(item_url(1), CHALLENGE)
    store = browser_store(tmp_path, plain, browser, sleeps)

    store.get(item_url(1))

    assert store.get(item_url(2)).via == "plain"
    assert browser.requests == [item_url(1)]


def test_consecutive_challenges_switch_the_rest_of_the_run(
    tmp_path, plain, browser, sleeps
):
    for n in range(1, 6):
        plain.reply(item_url(n), CHALLENGE)
    store = browser_store(tmp_path, plain, browser, sleeps, consecutive_challenges=5)

    for n in range(1, 6):
        store.get(item_url(n))
    page = store.get(item_url(6))

    assert page.via == "browser"
    assert item_url(6) not in plain.requests


def test_a_high_challenge_ratio_switches_the_rest_of_the_run(
    tmp_path, plain, browser, sleeps
):
    challenged = {2, 5, RATIO_MIN_SAMPLE}  # never 5 in a row; 3 of 10 is above 20%
    for n in challenged:
        plain.reply(item_url(n), CHALLENGE)
    store = browser_store(tmp_path, plain, browser, sleeps)

    for n in range(1, RATIO_MIN_SAMPLE + 1):
        expected = "browser" if n in challenged else "plain"
        assert store.get(item_url(n)).via == expected

    assert store.get(item_url(RATIO_MIN_SAMPLE + 1)).via == "browser"
    assert item_url(RATIO_MIN_SAMPLE + 1) not in plain.requests


def test_the_ratio_needs_a_minimum_sample(tmp_path, plain, browser, sleeps):
    plain.reply(item_url(1), CHALLENGE)  # 1 of 1 is far above 20%
    store = browser_store(tmp_path, plain, browser, sleeps)
    store.get(item_url(1))
    assert store.get(item_url(2)).via == "plain"


def test_each_run_starts_with_plain_fetching(tmp_path, plain, browser, sleeps):
    for n in range(1, 6):
        plain.reply(item_url(n), CHALLENGE)
    first_run = browser_store(tmp_path, plain, browser, sleeps)
    for n in range(1, 6):
        first_run.get(item_url(n))

    next_run = browser_store(tmp_path, plain, browser, sleeps)
    assert next_run.get(item_url(6)).via == "plain"


def test_a_challenge_the_browser_cannot_clear_stops_the_run(
    tmp_path, plain, browser, sleeps
):
    plain.reply(BOW, CHALLENGE)
    browser.reply(BOW, CHALLENGE)
    store = browser_store(tmp_path, plain, browser, sleeps)

    with pytest.raises(ChallengeError, match="not cleared in the browser"):
        store.get(BOW)
    assert list(store.iter_cached()) == []


@pytest.mark.skipif(
    importlib.util.find_spec("playwright") is not None, reason="playwright installed"
)
def test_browser_fallback_without_playwright_stops_the_run(tmp_path, plain, sleeps):
    plain.reply(BOW, CHALLENGE)
    config = make_config(tmp_path, browser=BrowserPolicy(enabled=True))
    store = PageStore(config, transport=plain, sleep=sleeps)

    with pytest.raises(RunStoppedError, match="Playwright"):
        store.get(BOW)


# ── Scraper config ───────────────────────────────────────────────────────────


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "scraper.yaml"
    path.write_text(text)
    return path


def test_the_committed_config_loads():
    config = load_scraper_config()
    assert config.crawl_delay_seconds >= 4
    assert config.cache_dir.is_absolute()


def test_relative_cache_dir_is_resolved_against_the_config_file(tmp_path):
    path = write_config(tmp_path, "user_agent: x\ncache_dir: pages\n")
    assert load_scraper_config(path).cache_dir == (tmp_path / "pages").resolve()


def test_crawl_delay_below_four_seconds_is_rejected(tmp_path):
    path = write_config(
        tmp_path, "user_agent: x\ncache_dir: p\ncrawl_delay_seconds: 3\n"
    )
    with pytest.raises(ScraperConfigError, match="crawl_delay_seconds"):
        load_scraper_config(path)


def test_unknown_config_keys_are_rejected(tmp_path):
    path = write_config(tmp_path, "user_agent: x\ncache_dir: p\nmax_concurrent: 3\n")
    with pytest.raises(ScraperConfigError, match="max_concurrent"):
        load_scraper_config(path)


def test_missing_config_file_is_a_config_error(tmp_path):
    with pytest.raises(ScraperConfigError):
        load_scraper_config(tmp_path / "absent.yaml")
