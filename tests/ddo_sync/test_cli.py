"""The ``ddoloot`` console script, through ``main(argv)``.

The wiki is never contacted: the fetcher and the MediaWiki API client are replaced with
fakes, and every path the CLI writes to points into a temporary directory.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ddo_sync.cli import main
from ddo_sync.models import ItemLink
from ddo_sync.queue_db import QueueRepository
from tests.ddo_sync.conftest import ITEM_PAGE_HTML, PAGES, UPDATE_PAGE_HTML

PAGE = "Update_5_named_items"


class FakeFetcher:
    """Stands in for WikiFetcher: serves canned pages and records requested URLs."""

    def __init__(self, item_html: str = ITEM_PAGE_HTML) -> None:
        self.item_html = item_html
        self.config = None
        self.urls: list[str] = []

    def __call__(self, config):
        self.config = config
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def fetch_url(self, url: str) -> str:
        self.urls.append(url)
        return self.item_html if "/page/Item:" in url else UPDATE_PAGE_HTML


@pytest.fixture(autouse=True)
def paths(tmp_path, monkeypatch):
    """Point the CLI's data, queue, cache and output paths at tmp_path."""
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    monkeypatch.setattr("ddo_sync.cli.DATA_DIR", data)
    monkeypatch.setattr("ddo_sync.cli.QUEUE_DB", data / "queue.db")
    monkeypatch.setattr("ddo_sync.cli.CACHE_DIR", cache)
    monkeypatch.setattr("ddo_sync.cli.EXTRACTED_DIR", cache / "extracted")
    monkeypatch.setattr("ddo_sync.cli._install_sigint_handler", lambda: None)
    return {"queue_db": data / "queue.db", "cache": cache, "out": cache / "extracted"}


@pytest.fixture(autouse=True)
def no_wiki_api():
    """The syncer's default MediaWiki client, replaced so no request is made."""
    client = MagicMock()
    client.get_last_modified.return_value = None
    with patch("ddo_sync.syncer.WikiApiClient", return_value=client):
        yield client


def run_sync(*args: str, fetcher: FakeFetcher | None = None) -> int:
    with patch("ddo_sync.cli.WikiFetcher", fetcher or FakeFetcher()):
        return main(["sync", *args])


# ── Top level ─────────────────────────────────────────────────────────────────


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "sync" in out
    assert "extract-item" in out


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_sync_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main(["sync", "--status", "--discover"])


# ── sync ──────────────────────────────────────────────────────────────────────


def test_sync_pages_writes_scraped_items(paths):
    fetcher = FakeFetcher()
    assert run_sync("--page", "Update 5 named items", fetcher=fetcher) == 0

    assert fetcher.urls[0] == f"https://ddowiki.com/page/{PAGE}"
    written = sorted(p.name for p in paths["out"].glob("*.json"))
    assert written == [
        "Item_Ring_of_Fire.json",
        "Item_Shield_of_Light.json",
        "Item_Sword_of_Shadow.json",
    ]
    item = json.loads((paths["out"] / "Item_Sword_of_Shadow.json").read_text())
    assert item["wiki"]["url"] == "https://ddowiki.com/page/Item:Sword_of_Shadow"
    assert len((paths["out"] / "report.jsonl").read_text().splitlines()) == 3


def test_sync_crawl_delay_is_never_below_four_seconds():
    fetcher = FakeFetcher()
    run_sync("--page", PAGE, "--rate-limit", "1", fetcher=fetcher)
    assert fetcher.config.rate_limit_delay == 4.0


def test_sync_limit_caps_processed_items(paths):
    assert run_sync("--page", PAGE, "--limit", "1") == 0
    assert len(list(paths["out"].glob("*.json"))) == 1


def test_sync_with_failed_items_exits_two(paths):
    fetcher = FakeFetcher(item_html="<html><body>no infobox</body></html>")
    assert run_sync("--page", PAGE, fetcher=fetcher) == 2
    with QueueRepository(str(paths["queue_db"])) as qr:
        assert qr.get_queue_stats().failed == 3


def test_sync_discovers_pages_when_none_given(paths):
    with patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer:
        discoverer.return_value.discover.return_value = [PAGE]
        assert run_sync() == 0
    assert len(list(paths["out"].glob("*.json"))) == 3


def test_sync_discovery_failure_exits_one():
    with patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer:
        discoverer.return_value.discover.side_effect = RuntimeError("down")
        assert run_sync() == 1


def test_sync_with_nothing_discovered_exits_zero():
    with patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer:
        discoverer.return_value.discover.return_value = []
        assert run_sync() == 0


def test_sync_interrupted_exits_one():
    fetcher = FakeFetcher()
    fetcher.fetch_url = MagicMock(side_effect=KeyboardInterrupt)
    assert run_sync("--page", PAGE, fetcher=fetcher) == 1


def test_sync_status_without_queue_db():
    assert main(["sync", "--status"]) == 0


def test_sync_status_with_queue_db(paths):
    paths["queue_db"].parent.mkdir(parents=True)
    with QueueRepository(str(paths["queue_db"])) as qr:
        qr.register_update_page(PAGE, f"https://ddowiki.com/page/{PAGE}")
        qr.mark_page_synced(PAGE, datetime(2025, 11, 1, tzinfo=timezone.utc))
    assert main(["sync", "--status", "--verbose"]) == 0


def test_sync_reset_failed_returns_failed_items_to_pending(paths):
    paths["queue_db"].parent.mkdir(parents=True)
    link = ItemLink(
        item_name="Sword",
        wiki_url="https://ddowiki.com/page/Item:Sword",
        update_page=PAGE,
    )
    with QueueRepository(str(paths["queue_db"])) as qr:
        qr.register_update_page(PAGE, f"https://ddowiki.com/page/{PAGE}")
        qr.enqueue_items([link])
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


# ── extract-item ──────────────────────────────────────────────────────────────


def _cache_with(paths, filename: str, name: str, url: str) -> None:
    (paths["cache"] / "html").mkdir(parents=True)
    shutil.copy(PAGES / filename, paths["cache"] / "html" / filename)
    index = {
        filename: {"name": name, "url": url, "update_page": "Update_32_named_items"}
    }
    (paths["cache"] / "index.json").write_text(json.dumps(index))


def test_extract_item_reads_the_local_cache_by_name(paths, capsys):
    url = "https://ddowiki.com/page/Item:Breaker_of_Bodies"
    _cache_with(paths, "Item_Breaker_of_Bodies.html", "Breaker of Bodies", url)

    assert main(["extract-item", "breaker of bodies"]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["item"]["name"] == "Breaker of Bodies"
    assert out["item"]["wiki"]["url"] == url
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


def test_extract_item_not_in_cache_exits_one(capsys):
    assert main(["extract-item", "No Such Item"]) == 1
    assert capsys.readouterr().out == ""


def test_extract_item_page_without_infobox_exits_one(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html><body><p>not an item</p></body></html>")
    assert main(["extract-item", "Whatever", "--html", str(html)]) == 1


def test_extract_item_makes_no_network_request():
    html = PAGES / "Item_Epic_Whirling_Words.html"
    with (
        patch("ddo_sync.cli.WikiFetcher") as fetcher,
        patch("ddo_sync.cli.UpdatePageDiscoverer") as discoverer,
    ):
        assert main(["extract-item", "Epic Whirling Words", "--html", str(html)]) == 0
    fetcher.assert_not_called()
    discoverer.assert_not_called()
