"""The ``ddoloot`` console script, through ``main(argv)``.

The wiki is never contacted: the Page Store is replaced with an in-memory fake (or a real
Page Store over a canned transport), the MediaWiki API client with a fake, and every path
the CLI writes to points into a temporary directory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ddo_sync.cli import main
from ddo_sync.models import ItemLink
from ddo_sync.queue_db import QueueRepository
from page_store import ChallengeError, PageStore
from tests.canned import CHALLENGE, CannedTransport, ok
from tests.ddo_sync.conftest import PAGES, InMemoryPageStore, serve_wiki

PAGE = "Update_5_named_items"
BREAKER_URL = "https://ddowiki.com/page/Item:Breaker_of_Bodies"


class FakePageStore(InMemoryPageStore):
    """Stands in for the PageStore class: ``PageStore(config)`` returns this instance."""

    def __init__(self, serve=serve_wiki) -> None:
        super().__init__(serve)
        self.config = None

    def __call__(self, config):
        self.config = config
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None


@pytest.fixture(autouse=True)
def paths(tmp_path, monkeypatch):
    """Point the CLI's data, queue, cache and output paths at tmp_path."""
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    config = tmp_path / "scraper.yaml"
    config.write_text("user_agent: ddoloot-test\ncache_dir: cache/pages\n")
    monkeypatch.setattr("ddo_sync.cli.DATA_DIR", data)
    monkeypatch.setattr("ddo_sync.cli.QUEUE_DB", data / "queue.db")
    monkeypatch.setattr("ddo_sync.cli.CACHE_DIR", cache)
    monkeypatch.setattr("ddo_sync.cli.EXTRACTED_DIR", cache / "extracted")
    monkeypatch.setattr("ddo_sync.cli._install_sigint_handler", lambda: None)
    return {
        "queue_db": data / "queue.db",
        "cache": cache,
        "pages": cache / "pages",
        "out": cache / "extracted",
        "config": config,
    }


@pytest.fixture(autouse=True)
def no_wiki_api():
    """The syncer's default MediaWiki client, replaced so no request is made."""
    client = MagicMock()
    client.get_last_modified.return_value = None
    with patch("ddo_sync.syncer.WikiApiClient", return_value=client):
        yield client


def run_sync(paths, *args: str, store: FakePageStore | None = None) -> int:
    with patch("ddo_sync.cli.PageStore", store or FakePageStore()):
        return main(["sync", "--scraper-config", str(paths["config"]), *args])


def canned_store(transport: CannedTransport):
    """A real PageStore class stand-in whose transport is *transport* and sleep a no-op."""

    def factory(config):
        return PageStore(config, transport=transport, sleep=lambda _s: None)

    return factory


def seed_queue(db: Path, name: str, url: str, update: str = PAGE) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with QueueRepository(str(db)) as qr:
        qr.register_update_page(update, f"https://ddowiki.com/page/{update}")
        qr.enqueue_items([ItemLink(item_name=name, wiki_url=url, update_page=update)])


# ── Top level ─────────────────────────────────────────────────────────────────


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "sync" in out
    assert "extract-item" in out
    assert "sample" in out


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_sync_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main(["sync", "--status", "--discover"])


# ── sync ──────────────────────────────────────────────────────────────────────


def test_sync_pages_writes_scraped_items(paths):
    store = FakePageStore()
    assert run_sync(paths, "--page", "Update 5 named items", store=store) == 0

    assert store.requests[0] == f"https://ddowiki.com/page/{PAGE}"
    assert store.config.cache_dir == (paths["config"].parent / "cache/pages").resolve()
    written = sorted(p.name for p in paths["out"].glob("*.json"))
    assert written == [
        "Item_Ring_of_Fire.json",
        "Item_Shield_of_Light.json",
        "Item_Sword_of_Shadow.json",
    ]
    item = json.loads((paths["out"] / "Item_Sword_of_Shadow.json").read_text())
    assert item["wiki"]["url"] == "https://ddowiki.com/page/Item:Sword_of_Shadow"
    assert len((paths["out"] / "report.jsonl").read_text().splitlines()) == 3


def test_sync_rate_limit_flag_is_gone(paths):
    with pytest.raises(SystemExit):
        run_sync(paths, "--page", PAGE, "--rate-limit", "4")


def test_sync_refuses_a_crawl_delay_below_four_seconds(paths):
    paths["config"].write_text(
        "user_agent: ddoloot-test\ncache_dir: pages\ncrawl_delay_seconds: 1\n"
    )
    store = FakePageStore()
    assert run_sync(paths, "--page", PAGE, store=store) == 1
    assert store.config is None
    assert store.requests == []


def test_sync_reads_held_pages_without_requests_unless_refresh(paths):
    store = FakePageStore()
    update_url = f"https://ddowiki.com/page/{PAGE}"
    assert run_sync(paths, "--page", PAGE, store=store) == 0
    store.requests.clear()

    assert run_sync(paths, "--page", PAGE, store=store) == 0
    assert store.requests == []

    assert run_sync(paths, "--page", PAGE, "--refresh", store=store) == 0
    assert store.requests == [update_url]


def test_sync_challenge_stops_the_run_and_marks_nothing_failed(paths):
    def serve(url: str) -> str:
        if "/page/Item:" in url:
            raise ChallengeError("WAF challenge", url=url)
        return serve_wiki(url)

    assert run_sync(paths, "--page", PAGE, store=FakePageStore(serve)) == 1
    with QueueRepository(str(paths["queue_db"])) as qr:
        stats = qr.get_queue_stats()
    assert stats.failed == 0
    assert stats.in_progress == 0
    assert stats.pending == 3


def test_sync_uses_the_queue_db_flag(paths, tmp_path):
    other = tmp_path / "elsewhere" / "scratch.db"
    assert run_sync(paths, "--page", PAGE, "--queue-db", str(other)) == 0
    assert other.exists()
    assert not paths["queue_db"].exists()


def test_sync_limit_caps_processed_items(paths):
    assert run_sync(paths, "--page", PAGE, "--limit", "1") == 0
    assert len(list(paths["out"].glob("*.json"))) == 1


def test_sync_with_failed_items_exits_two(paths):
    store = FakePageStore(
        lambda url: (
            "<html><body>no infobox</body></html>"
            if "/page/Item:" in url
            else serve_wiki(url)
        )
    )
    assert run_sync(paths, "--page", PAGE, store=store) == 2
    with QueueRepository(str(paths["queue_db"])) as qr:
        assert qr.get_queue_stats().failed == 3


def test_sync_discovers_pages_when_none_given(paths):
    with patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer:
        discoverer.return_value.discover.return_value = [PAGE]
        assert run_sync(paths) == 0
    assert len(list(paths["out"].glob("*.json"))) == 3


def test_sync_discovery_failure_exits_one(paths):
    with patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer:
        discoverer.return_value.discover.side_effect = RuntimeError("down")
        assert run_sync(paths) == 1


def test_sync_with_nothing_discovered_exits_zero(paths):
    with patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer:
        discoverer.return_value.discover.return_value = []
        assert run_sync(paths) == 0


def test_sync_interrupted_exits_one(paths):
    def serve(_url: str) -> str:
        raise KeyboardInterrupt

    assert run_sync(paths, "--page", PAGE, store=FakePageStore(serve)) == 1


def test_sync_status_without_queue_db():
    assert main(["sync", "--status"]) == 0


def test_sync_status_with_queue_db(paths):
    paths["queue_db"].parent.mkdir(parents=True)
    with QueueRepository(str(paths["queue_db"])) as qr:
        qr.register_update_page(PAGE, f"https://ddowiki.com/page/{PAGE}")
        qr.mark_page_synced(PAGE, datetime(2025, 11, 1, tzinfo=timezone.utc))
    assert main(["sync", "--status", "--verbose"]) == 0
    assert main(["sync", "--status", "--queue-db", str(paths["queue_db"])]) == 0


def test_sync_reset_failed_returns_failed_items_to_pending(paths):
    seed_queue(paths["queue_db"], "Sword", "https://ddowiki.com/page/Item:Sword")
    with QueueRepository(str(paths["queue_db"])) as qr:
        item = qr.get_pending_items()[0]
        qr.mark_failed(item.id, datetime.now(timezone.utc), "boom")

    assert main(["sync", "--reset-failed"]) == 0

    with QueueRepository(str(paths["queue_db"])) as qr:
        assert qr.get_queue_stats().pending == 1


def test_sync_reset_failed_without_queue_db():
    assert main(["sync", "--reset-failed"]) == 0


def test_sync_discover_lists_pages():
    with patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer:
        discoverer.return_value.discover.return_value = [PAGE]
        assert main(["sync", "--discover"]) == 0
        discoverer.return_value.discover.side_effect = RuntimeError("down")
        assert main(["sync", "--discover"]) == 1


# ── sample ────────────────────────────────────────────────────────────────────


def run_sample(paths, transport: CannedTransport, *args: str) -> int:
    with patch("ddo_sync.cli.PageStore", canned_store(transport)):
        return main(
            [
                "sample",
                "--scraper-config",
                str(paths["config"]),
                "--queue-db",
                str(paths["queue_db"]),
                *args,
            ]
        )


def test_sample_reads_queued_pages_into_the_page_store(paths):
    seed_queue(paths["queue_db"], "Breaker of Bodies", BREAKER_URL)
    html = (PAGES / "Item_Breaker_of_Bodies.html").read_text(encoding="utf-8")
    transport = CannedTransport().reply(BREAKER_URL, ok(html))
    transport.reply("https://ddowiki.com/robots.txt", ok("User-agent: *\nAllow: /\n"))

    assert run_sample(paths, transport, "--count", "1") == 0
    assert transport.requests[-1] == BREAKER_URL
    assert "/api.php" not in " ".join(transport.requests)

    assert len(list(paths["pages"].glob("*.html"))) == 1

    # A second sample costs no page request.
    transport.requests.clear()
    assert run_sample(paths, transport, "--count", "1") == 0
    assert BREAKER_URL not in transport.requests


def test_sample_without_a_queue_db_exits_one(paths):
    assert run_sample(paths, CannedTransport(), "--count", "1") == 1


def test_sample_challenge_with_browser_disabled_exits_one(paths):
    seed_queue(paths["queue_db"], "Breaker of Bodies", BREAKER_URL)
    transport = CannedTransport(default=lambda _url: CHALLENGE)

    assert run_sample(paths, transport, "--count", "1") == 1
    assert not list(paths["pages"].glob("*.html"))


def test_sample_with_a_page_that_cannot_be_fetched_exits_two(paths):
    seed_queue(paths["queue_db"], "Breaker of Bodies", BREAKER_URL)
    assert run_sample(paths, CannedTransport(), "--count", "1") == 2


# ── extract-item ──────────────────────────────────────────────────────────────


def test_extract_item_reads_the_page_store_by_name(paths, capsys):
    html = (PAGES / "Item_Breaker_of_Bodies.html").read_text(encoding="utf-8")
    config_args = ["--scraper-config", str(paths["config"])]
    with patch(
        "ddo_sync.cli.PageStore",
        canned_store(CannedTransport().reply(BREAKER_URL, ok(html))),
    ):
        seed_queue(paths["queue_db"], "Breaker of Bodies", BREAKER_URL)
        main(["sample", "--queue-db", str(paths["queue_db"]), *config_args])

    assert main(["extract-item", "breaker of bodies", *config_args]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["item"]["name"] == "Breaker of Bodies"
    assert out["item"]["wiki"]["url"] == BREAKER_URL
    assert out["item"]["extraction_errors"] == {}
    assert out["report"]["template"] == "shield"


def test_extract_item_reads_a_saved_html_file(capsys):
    html = PAGES / "Item_Dark_Ressurectionist_s_Frock_Vest.html"
    assert (
        main(["extract-item", "Dark Ressurectionist's Frock Vest", "--html", str(html)])
        == 0
    )

    item = json.loads(capsys.readouterr().out)["item"]
    assert item["effects"] == []
    assert item["wiki"]["url"] == (
        "https://ddowiki.com/page/Item:Dark_Ressurectionist%27s_Frock_Vest"
    )


def test_extract_item_not_held_exits_one(paths, capsys):
    args = ["extract-item", "No Such Item", "--scraper-config", str(paths["config"])]
    assert main(args) == 1
    assert capsys.readouterr().out == ""


def test_extract_item_page_without_infobox_exits_one(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html><body><p>not an item</p></body></html>")
    assert main(["extract-item", "Whatever", "--html", str(html)]) == 1


def test_extract_item_makes_no_network_request(paths):
    html = PAGES / "Item_Epic_Whirling_Words.html"
    transport = CannedTransport()
    with (
        patch("ddo_sync.cli.PageStore", canned_store(transport)),
        patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer,
    ):
        assert main(["extract-item", "Epic Whirling Words", "--html", str(html)]) == 0
        main(
            [
                "extract-item",
                "Epic Whirling Words",
                "--scraper-config",
                str(paths["config"]),
            ]
        )
    assert transport.requests == []
    discoverer.assert_not_called()
