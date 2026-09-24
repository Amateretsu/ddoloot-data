# ddo_sync

Orchestration layer that discovers DDO Wiki update pages, keeps a persistent crawl queue, and drives the fetch → extract → write pipeline that turns item pages into Scraped Items. Also provides the `ddoloot` command.

---

## Overview

The sync pipeline works in three stages:

1. **Discover**: `UpdatePageDiscoverer` finds the `Update_<N>_named_items` pages.
2. **Queue**: `UpdatePageParser` extracts item links from each update page, and `QueueRepository` stores them in the SQLite crawl queue (`data/queue.db`).
3. **Process**: `DDOSyncer` works through the queue. For each item it reads the page through the Page Store (`docs/page_store.md`), calls `item_extractor.extract()` in-process to get a Scraped Item and its report, and hands both to a `ScrapedItemWriterProtocol` adapter. In production this adapter is `JsonItemWriter`, which writes under `cache/extracted/`.

Every page, whether an update page or an item page, is read through the Page Store. A page the store already holds costs no request, and `--refresh` is the only way to refetch one. When the Page Store stops the run (`page_store.RunStoppedError`, for example a WAF challenge with the browser fallback disabled), the sync ends with exit code 1. Nothing is marked failed and the current item stays pending, so a rerun resumes from the queue.

Items that fail (a fetch error, or a page with no item infobox) are retried up to `max_retries` times before they stay failed. `ddoloot sync --reset-failed` clears that state.

> Discovery and the last-modified check still use the MediaWiki API through `WikiApiClient`. ADR 0006 forbids that API, and a later step replaces it with HTML discovery through the Page Store. Until then, a stale update page is re-read from the Page Store's copy unless `--refresh` is given. `ddoloot sample` and `extract-item` never touch the API.

---

## CLI

Install the package with `pip install -e .` to get the `ddoloot` command. It has three subcommands. `--verbose` (DEBUG logging) goes after any of them.

Common options:

| Flag | Subcommands | Description |
|---|---|---|
| `--scraper-config PATH` | all | Scraper policy and Page Store location. Default: `config/scraper.yaml`. See `docs/page_store.md`. |
| `--queue-db PATH` | `sync`, `sample` | SQLite crawl queue. Default: `data/queue.db`. |

### `ddoloot sync`

```
ddoloot sync [--status | --discover | --reset-failed]
             [--page PAGE [PAGE …]]
             [--limit N]
             [--refresh]
             [--max-retries N]
             [--scraper-config PATH] [--queue-db PATH]
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
| `--refresh` | Refetch every page this run reads, even pages the Page Store already holds |
| `--max-retries N` | Attempts before an item stays failed (default: 3) |

Output:

- `cache/extracted/<page-slug>.json`: one Scraped Item per item page, for example `Item_Breaker_of_Bodies.json`. Every field is written: null means the wiki row was absent or said "None", and a field that could not be parsed is null with its raw text in `extraction_errors`.
- `cache/extracted/report.jsonl`: one line per extracted page (`name`, `url`, `template`, unmapped rows, ignored rows, rule hits, unclassified Effects). Each run appends to it.
- `data/queue.db` (or `--queue-db`): crawl queue and update-page sync state.
- The Page Store's `cache_dir` (default `cache/pages/`): every page fetched.

`cache/` and `data/` are gitignored.

Pacing, retries, robots.txt, challenge handling and the browser fallback are all set in the scraper config. The crawl delay is at least 4 s (ADR 0006), and a config below that is rejected.

**Exit codes:** `0` = success · `1` = fatal error, or the run was stopped by the acquisition policy · `2` = completed with failed items

### `ddoloot sample`

```
ddoloot sample [--count N] [--seed N] [--scraper-config PATH] [--queue-db PATH] [--verbose]
```

Picks up to `--count` item pages (default 40) from the crawl queue. Items count whatever their status. The pick is spread across as many update pages as possible: update pages are shuffled with `--seed` (default 1) and visited round-robin. Each picked page is read through the Page Store, so pages it already holds cost nothing. The point is a varied local sample for offline extractor work. It never calls the MediaWiki API.

**Exit codes:** `0` = every page held · `1` = no queue database, bad config, or the run was stopped (e.g. a challenge with the browser fallback disabled) · `2` = some pages could not be fetched

### `ddoloot extract-item`

```
ddoloot extract-item NAME [--html PATH] [--scraper-config PATH] [--verbose]
```

Extracts one item page and prints `{"item": <Scraped Item>, "report": <report>}` as JSON. It never touches the network.

- Without `--html`, NAME is matched, ignoring case and with or without the `Item:` prefix, against the titles of the pages the Page Store holds (`iter_cached()`).
- With `--html PATH`, the page is read from that file and the URL is `https://ddowiki.com/page/Item:<NAME>`.

It exits `1` if the Page Store does not hold the page or the page has no item infobox.

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

# Refetch pages the Page Store already holds
ddoloot sync --page "Update_69_named_items" --refresh

# Use another policy file (e.g. a slower crawl delay or the browser fallback)
ddoloot sync --scraper-config my-scraper.yaml

# Sample 40 item pages across update pages into the Page Store
ddoloot sample --count 40

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
| `page_store` | `PageStoreProtocol.get(url, refresh=False) -> CachedPage` | `page_store.PageStore` |
| `writer` | `ScrapedItemWriterProtocol.write(item, report)` | `JsonItemWriter` |
| `queue_repo` | `QueueRepositoryProtocol` | `QueueRepository` |
| `max_retries` | (default 3) | |
| `api_client` | `WikiApiClientProtocol` (optional) | `WikiApiClient` |
| `parser` | `UpdatePageParserProtocol` (optional) | `UpdatePageParser` |
| `extractor_config` | extractor rules (optional) | `item_extractor.load_config()` (`catalog/extractor/`) |
| `refresh` | (default False) | refetch every page instead of using held copies |

The extractor itself is not a seam: `DDOSyncer` calls `item_extractor.extract()` directly.

```python
from pathlib import Path

from ddo_sync import DDOSyncer, JsonItemWriter, QueueRepository
from page_store import PageStore, load_scraper_config

with (
    PageStore(load_scraper_config()) as store,
    QueueRepository("data/queue.db") as queue_repo,
):
    syncer = DDOSyncer(
        page_store=store,
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
| `sync_update_page(page_name) -> list[ItemLink]` | Read one update page through the Page Store, enqueue its item links, and record `last_synced_at`. It does not process the queue. Raises `UpdatePageError`, or lets `RunStoppedError` through. |
| `process_queue(limit=None) -> (success, failures)` | For each pending item: read the page through the Page Store, `extract()`, `writer.write()`, then mark complete. Any other exception marks that item failed, and processing continues. `RunStoppedError` propagates and leaves the item pending. |
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

## sample_pages

The function behind `ddoloot sample`.

```python
from ddo_sync import QueueRepository, sample_pages

with PageStore(load_scraper_config()) as store, QueueRepository("data/queue.db") as repo:
    for result in sample_pages(repo, store, count=40, seed=1):
        result.item, result.page, result.error   # QueueItem, CachedPage | None, str | None
```

A page that cannot be fetched is returned with `error` set, and sampling carries on. `RunStoppedError` propagates.

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

Extraction failures (`item_extractor.ExtractionError`) and Page Store `FetchError`s do not escape `process_queue`: the item is marked failed. `page_store.RunStoppedError` does escape it, and the item stays pending.
