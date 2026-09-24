# ddo_sync

Orchestration layer that discovers DDO Wiki update pages, keeps a persistent crawl queue, and drives the fetch → extract → write pipeline that turns item pages into Scraped Items. Also provides the `ddoloot` command.

---

## Overview

The sync pipeline works in three stages:

1. **Discover**: `UpdatePageDiscoverer` finds the `Update_<N>_named_items` pages.
2. **Queue**: `UpdatePageParser` extracts item links from each update page, and `QueueRepository` stores them in the SQLite crawl queue (`data/queue.db`).
3. **Process**: `DDOSyncer` works through the queue. For each item it fetches the page, calls `item_extractor.extract()` in-process to get a Scraped Item and its report, and hands both to a `ScrapedItemWriterProtocol` adapter. In production this adapter is `JsonItemWriter`, which writes under `cache/extracted/`.

Items that fail (fetch error, or a page with no item infobox) are retried up to `max_retries` times before they stay failed. `ddoloot sync --reset-failed` clears that state.

> Discovery and the last-modified check still use the MediaWiki API through `WikiApiClient`. ADR 0006 forbids that API, and a later step replaces it with HTML discovery through the Page Store.

---

## CLI

Install the package with `pip install -e .` to get the `ddoloot` command. It has two subcommands. `--verbose` (DEBUG logging) goes after either of them.

### `ddoloot sync`

```
ddoloot sync [--status | --discover | --reset-failed]
             [--page PAGE [PAGE …]]
             [--limit N]
             [--rate-limit SECONDS]
             [--max-retries N]
             [--verbose]
```

| Flag | Description |
|---|---|
| *(no flags)* | Full sync: discover all update pages, queue items, process the queue |
| `--status` | Print queue statistics and tracked update pages, then exit |
| `--discover` | List discoverable update pages without syncing |
| `--reset-failed` | Reset all failed queue items to pending, then exit |
| `--page NAME …` | Sync one or more specific update pages (spaces or underscores) |
| `--limit N` | Cap the number of items processed in this run |
| `--rate-limit SECONDS` | Seconds between requests. The default and the minimum are both 4 s (ADR 0006); lower values are raised to 4. |
| `--max-retries N` | Attempts before an item stays failed (default: 3) |

Output:

- `cache/extracted/<page-slug>.json`: one Scraped Item per item page, for example `Item_Breaker_of_Bodies.json`. Every field is written: null means the wiki row was absent or said "None", and a field that could not be parsed is null with its raw text in `extraction_errors`.
- `cache/extracted/report.jsonl`: one line per extracted page (`name`, `url`, `template`, unmapped rows, ignored rows, rule hits, unclassified Effects). Each run appends to it.
- `data/queue.db`: crawl queue and update-page sync state.

`cache/` and `data/` are gitignored.

**Exit codes:** `0` = success · `1` = fatal error · `2` = completed with failed items

### `ddoloot extract-item`

```
ddoloot extract-item NAME [--html PATH] [--verbose]
```

Extracts one item page and prints `{"item": <Scraped Item>, "report": <report>}` as JSON. It never touches the network.

- Without `--html`, NAME is looked up (ignoring case) in the local page cache index `cache/index.json`, and the page is read from `cache/html/`.
- With `--html PATH`, the page is read from that file. The URL comes from the cache index if NAME is listed there, otherwise from `https://ddowiki.com/page/Item:<NAME>`.

It exits `1` if the page is not cached or has no item infobox.

### Examples

```bash
# Full sync
ddoloot sync

# Check what's been synced so far
ddoloot sync --status

# See which update pages exist without syncing anything
ddoloot sync --discover

# Sync two specific pages
ddoloot sync --page "Update_69_named_items" "Update_70_named_items"

# Process at most 20 items
ddoloot sync --limit 20

# Retry everything that failed in a previous run
ddoloot sync --reset-failed

# Slow down further than the 4 s minimum
ddoloot sync --rate-limit 6

# Inspect one cached page's Scraped Item
ddoloot extract-item "Legendary Gnollish War Bow"

# ...or a committed fixture page
ddoloot extract-item "Breaker of Bodies" --html tests/fixtures/pages/Item_Breaker_of_Bodies.html
```

---

## DDOSyncer

The main orchestrator. Its collaborators are injected through seams, so tests use in-memory fakes:

| Argument | Seam | Production adapter |
|---|---|---|
| `fetcher` | `FetcherProtocol.fetch_url(url) -> str` | `ddowiki_scraper.WikiFetcher` |
| `writer` | `ScrapedItemWriterProtocol.write(item, report)` | `JsonItemWriter` |
| `queue_repo` | `QueueRepositoryProtocol` | `QueueRepository` |
| `max_retries` | (default 3) | |
| `api_client` | `WikiApiClientProtocol` (optional) | `WikiApiClient` |
| `parser` | `UpdatePageParserProtocol` (optional) | `UpdatePageParser` |
| `extractor_config` | extractor rules (optional) | `item_extractor.load_config()` (`catalog/extractor/`) |

The extractor itself is not a seam: `DDOSyncer` calls `item_extractor.extract()` directly.

```python
from pathlib import Path

from ddowiki_scraper import WikiFetcher, WikiFetcherConfig
from ddo_sync import DDOSyncer, JsonItemWriter, QueueRepository

config = WikiFetcherConfig(rate_limit_delay=4.0)
with WikiFetcher(config) as fetcher, QueueRepository("data/queue.db") as queue_repo:
    syncer = DDOSyncer(
        fetcher=fetcher,
        writer=JsonItemWriter(Path("cache/extracted")),
        queue_repo=queue_repo,
        max_retries=3,
    )
    syncer.register_update_page("Update_69_named_items")
    status = syncer.sync_all()
    print(status.queue_stats.complete, status.queue_stats.failed)
```

### Methods

| Method | Description |
|---|---|
| `register_update_page(page_name) -> None` | Track an update page (spaces become underscores). Safe to call repeatedly. |
| `sync_update_page(page_name) -> list[ItemLink]` | Fetch one update page, enqueue its item links, and record `last_synced_at`. It does not process the queue. Raises `UpdatePageError`. |
| `process_queue(limit=None) -> (success, failures)` | For each pending item: fetch, `extract()`, `writer.write()`, then mark complete. Any exception marks that item failed, and processing continues. |
| `sync_all() -> SyncStatus` | Reset failed items below `max_retries`, re-sync stale update pages, process the whole queue. |
| `get_status() -> SyncStatus` | Queue stats plus per-page status. |

---

## JsonItemWriter

The production adapter behind `ScrapedItemWriterProtocol`.

```python
from pathlib import Path
from ddo_sync import JsonItemWriter

writer = JsonItemWriter(Path("cache/extracted"))
writer.write(item, report)   # item: item_extractor.ScrapedItem, report: dict
```

`write()` creates the directory if needed. It writes `<page-slug>.json`, where the slug is the page title from `item.wiki.url` with every run of non-alphanumeric characters replaced by `_`. It also appends `{"name", "url", **report}` as one line to `report.jsonl`.

---

## UpdatePageDiscoverer

Finds update pages and returns them sorted numerically:

```python
from ddo_sync import UpdatePageDiscoverer

pages = UpdatePageDiscoverer().discover()
# ["Update_5_named_items", ..., "Update_79_named_items"]
```

**Raises:** `WikiApiError` on HTTP failure or a malformed response.

---

## UpdatePageParser

Extracts item links from rendered update-page HTML.

```python
from ddo_sync import UpdatePageParser

parser = UpdatePageParser(base_url="https://ddowiki.com")
links = parser.parse(html, "Update_69_named_items")
# [ItemLink(item_name="Sword of Shadow",
#           wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow",
#           update_page="Update_69_named_items"), ...]
```

Links are deduplicated, and only `/page/Item:` links are returned.

---

## QueueRepository

The SQLite crawl queue: update pages and the items discovered on them, with per-item status.

```python
from ddo_sync import QueueRepository

with QueueRepository("data/queue.db") as repo:
    repo.register_update_page("Update_69_named_items",
                              "https://ddowiki.com/page/Update_69_named_items")
    repo.enqueue_items(links)
    pending = repo.get_pending_items(limit=10)
    stats = repo.get_queue_stats()
```

| Method | Description |
|---|---|
| `register_update_page(page_name, page_url)` | Track an update page |
| `mark_page_synced(page_name, synced_at)` / `set_wiki_modified_at(page_name, modified_at)` | Sync-state timestamps |
| `get_update_page_status(page_name)` / `list_update_pages()` | Read update-page state |
| `enqueue_items(links) -> int` | Add item links, ignoring ones already queued; returns the number inserted |
| `mark_in_progress` / `mark_complete` / `mark_failed` / `mark_skipped` | Item status transitions |
| `reset_failed_to_pending(max_retries) -> int` | Retry failed items below the retry cap |
| `get_pending_items(limit=None)` / `get_queue_stats()` / `get_items_for_update_page(page_name)` | Reads |

---

## Data models

| Model | Fields |
|---|---|
| `ItemLink` | `item_name`, `wiki_url`, `update_page` |
| `QueueItem` | `id`, `item_name`, `wiki_url`, `update_page`, `status`, `queued_at`, `started_at`, `completed_at`, `error_message`, `retry_count` |
| `QueueStats` | `pending`, `in_progress`, `complete`, `failed`, `skipped` (plus `total`) |
| `UpdatePageStatus` | `page_name`, `page_url`, `last_synced_at`, `wiki_modified_at` (plus `needs_resync`) |
| `SyncStatus` | `queue_stats`, `update_pages` (by page name) |

---

## Exceptions

All exceptions inherit from `DDOSyncError`.

| Exception | When raised |
|---|---|
| `DDOSyncError` | Base class |
| `UpdatePageError` | Failed to fetch or parse an update page |
| `WikiApiError` | MediaWiki API request failed or returned an unexpected structure |
| `QueueDbError` | Queue database operation failed |
| `QueueSchemaError` | Queue schema could not be applied |

Extraction failures (`item_extractor.ExtractionError`) do not escape `process_queue`: the item is marked failed.
