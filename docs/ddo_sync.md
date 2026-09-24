# ddo_sync

Orchestration layer that discovers DDO Wiki update pages, keeps a persistent crawl queue, and drives the fetch → extract → write pipeline that turns item pages into Scraped Items. Also provides the `ddoloot` command.

---

## Overview

The sync pipeline works in three stages:

1. **Discover**: the discovery module reads the named-items index page (`https://ddowiki.com/page/Named_items`) and collects every `Update_<N>_named_items` page it links to.
2. **Queue**: discovery reads each update page and lists its `Item:` links and its revision id. The queue module (`QueueRepository`) stores them in the SQLite crawl queue (`data/queue.db`).
3. **Process**: `DDOSyncer` works through the queue. For each item it reads the page through the Page Store (`docs/page_store.md`), calls `item_extractor.extract()` in-process to get a Scraped Item and its report, and hands both to a `ScrapedItemWriterProtocol` adapter. In production this adapter is `JsonItemWriter`, which writes under `cache/extracted/<update>/`.

Discovery reads rendered `/page/` HTML only. It never uses the MediaWiki API (`/api.php`), which ADR 0006 forbids; the Page Store refuses such URLs anyway.

> The index page title `Named_items` has not been checked against the live wiki, because no live fetch was allowed when it was chosen. If `ddoloot sync --discover` fails with "links to no Update_<N>_named_items page", check that title first (`NAMED_ITEMS_INDEX_URL` in `src/ddo_sync/discovery.py`). Meanwhile `sync --page NAME …` works without the index.

Every page, whether the index, an update page or an item page, is read through the Page Store. A page the store already holds costs no request, and `--refresh` is the only way to refetch one. When the Page Store stops the run (`page_store.RunStoppedError`, for example a WAF challenge with the browser fallback disabled), the sync ends with exit code 1. Nothing is marked failed and the current item stays pending, so a rerun resumes from the queue.

Items that fail (a fetch error, or a page with no item infobox) are retried up to `max_retries` times before they stay failed. `ddoloot sync --reset-failed` clears that state.

### Revisions and staleness

Each sync reads every tracked update page again. A page the Page Store holds costs no request, so any item link not yet queued gets queued. The revision id of the copy that was read (`"wgCurRevisionId"` in the page HTML) is stored in `update_pages.revision_id`. Only the wiki can tell whether a page has changed, and asking means a request, so there is no "stale" flag. To pick up wiki edits, run with `--refresh`. When a refreshed update page has a new revision id, the syncer logs the old and new ids.

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
| *(no flags)* | Full sync: discover update pages from the index, queue items, process the queue |
| `--status` | Print queue statistics and tracked update pages (last read, revision id), then exit. Makes no request. |
| `--discover` | Read the named-items index through the Page Store and list its update pages, without syncing. Add `--refresh` to refetch the index. |
| `--reset-failed` | Reset all failed queue items to pending, then exit |
| `--page NAME …` | Sync one or more specific update pages (spaces or underscores) |
| `--limit N` | Cap the number of items processed in this run |
| `--refresh` | Refetch every page this run reads, even pages the Page Store already holds |
| `--max-retries N` | Attempts before an item stays failed (default: 3) |

Output:

- `cache/extracted/<update>/<page-slug>.json`: one Scraped Item per item page, for example `update-5/Item_Breaker_of_Bodies-1a2b3c4d.json`: a readable slug of the page title plus the first 8 hex digits of the title's SHA-1, so titles that differ only in punctuation or case never share a file. `<update>` comes from the update page the item was queued from (`Update_5_named_items` → `update-5`); anything else is `unknown`. An item listed on two update pages is written to both folders. Every field is written: null means the wiki row was absent or said "None", and a field that could not be parsed is null with its raw text in `extraction_errors`.
- `cache/extracted/<update>/report.jsonl`: one line per page (`name`, `url`, `update_page`, `template`, `unmapped_rows`, `ignored_rows`, `rule_hits`, `unclassified_effects`, `extraction_errors`, `warnings`). `unclassified_effects` lists Effects entries that no `enchantments.yaml` rule matched (they are left out of the item and never stop the run) or that reached the fallback rule while containing a digit. `warnings` holds notes such as a named set that has bonuses but no name (the set is kept with `name: null`). Writing a page again replaces its line, so the file always has exactly one line per JSON file beside it, across runs and `--limit` batches.
- `data/queue.db` (or `--queue-db`): crawl queue and update-page sync state.
- The Page Store's `cache_dir` (default `cache/pages/`): every page fetched.

`cache/` and `data/` are gitignored.

Pacing, retries, robots.txt, challenge handling and the browser fallback are all set in the scraper config. The crawl delay is at least 4 s (ADR 0006), and a config below that is rejected.

**Exit codes:** `0` = success · `1` = fatal error, or the run was stopped by the acquisition policy · `2` = completed with failed items

### `ddoloot sample`

```
ddoloot sample [--count N] [--seed N] [--scraper-config PATH] [--queue-db PATH] [--verbose]
```

Picks up to `--count` item pages (default 40) from the crawl queue. Items count whatever their status. The pick is spread across as many update pages as possible: update pages are shuffled with `--seed` (default 1) and visited round-robin. Each picked page is read through the Page Store, so pages it already holds cost nothing. The point is a varied local sample for offline extractor work.

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

# See which update pages the index lists (reads only the index page)
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
| `queue_repo` | (none: one implementation) | `QueueRepository` |
| `max_retries` | (default 3) | |
| `extractor_config` | extractor rules (optional) | `item_extractor.load_config()` (`catalog/extractor/`) |
| `refresh` | (default False) | refetch every page instead of using held copies |

The extractor, the discovery module and the queue are not seams: each has one implementation, used directly. Protocols exist only where two adapters do (Page Store and writer: real plus in-memory fake).

```python
from pathlib import Path

from ddo_sync import DDOSyncer, JsonItemWriter, QueueRepository, discover_update_pages
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
    for name in discover_update_pages(store):
        syncer.register_update_page(name)
    status = syncer.sync_all()
    print(status.queue_stats.complete, status.queue_stats.failed)
```

### Methods

| Method | Description |
|---|---|
| `register_update_page(page_name) -> None` | Track an update page (spaces become underscores). Safe to call repeatedly. |
| `sync_update_page(page_name) -> list[ItemLink]` | Read one update page through the Page Store (registering it if needed), enqueue its new item links, and record `last_synced_at` and the revision id read. It does not process the queue. Raises `UpdatePageError`, or lets `RunStoppedError` through. |
| `process_queue(limit=None) -> (success, failures)` | For each pending item: read the page through the Page Store, `extract()`, add the item's `update_page` to the report, `writer.write()`, then mark complete. Any other exception marks that item failed, and processing continues. `RunStoppedError` propagates and leaves the item pending. |
| `sync_all(limit=None) -> SyncStatus` | Reset failed items below `max_retries`, read every tracked update page (an unreadable one is logged and skipped), then process up to `limit` pending items. |
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

`write()` files the item under `<out_dir>/<update>/`, where `<update>` is `update_slug(report["update_page"])` (`update-8`, or `unknown` when the key is missing or is not an `Update_<N>_named_items` title). It writes `<page-slug>.json`, where the slug is the page title from `item.wiki.url` with every run of non-alphanumeric characters replaced by `_`, plus `-` and the first 8 hex digits of the title's SHA-1. In that folder's `report.jsonl` it replaces the page's line (matched by page slug, so `%27` and `'` spellings of one URL are one page) with `{"name", "url", "update_page", **report, "extraction_errors", "warnings"}`. Both files are written atomically.

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

## Discovery

`src/ddo_sync/discovery.py` walks rendered wiki pages through any `PageStoreProtocol`:

```python
from ddo_sync import discover_update_pages, read_update_page, update_slug

names = discover_update_pages(store)          # ["Update_5_named_items", "Update_8_named_items", ...]
page = read_update_page(store, names[0])      # UpdatePage(page_name, url, revision_id, links)
update_slug(page.page_name)                   # "update-5"
```

| Function | Description |
|---|---|
| `discover_update_pages(store, refresh=False) -> list[str]` | Reads `NAMED_ITEMS_INDEX_URL` and returns the `Update_<N>_named_items` pages it links to, deduplicated and sorted by update number. Category pages, `…_revamped_named_items` pages and red links do not count. Raises `UpdatePageError` if the index cannot be read or links to no update page. |
| `read_update_page(store, page_name, refresh=False) -> UpdatePage` | Reads one update page. It returns its `Item:` links in document order, deduplicated by URL (the URL is kept as the wiki encoded it, and the name is decoded), plus the `wgCurRevisionId` from the HTML, or `None` if that is missing. Raises `UpdatePageError` on a fetch error or empty HTML. |
| `update_page_url(page_name) -> str` | `https://ddowiki.com/page/<page_name>`, with spaces turned into underscores |
| `update_slug(page_name) -> str` | `Update_8_named_items` → `update-8`, anything else → `unknown` |

Only links inside the article body (`#mw-content-text`) count, so the skin's tabs, sidebar and footer are ignored. Links with a query string (edit links and red links) are skipped, and so are fragments. `RunStoppedError` from the Page Store always propagates.

---

## QueueRepository

The queue module (`src/ddo_sync/queue_db.py`) is one repository class over one SQLite file with two tables: `update_pages` (name, URL, `last_synced_at`, `revision_id`) and `scrape_queue` (one row per item link, with status and retry count). The database is local and disposable, so there are no migrations. A file from an older schema (checked with `PRAGMA user_version`) raises `QueueSchemaError`; delete the file and rerun.

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
| `mark_page_synced(page_name, synced_at, revision_id=None)` | Record when the page's links were read, and from which revision |
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
| `UpdatePageStatus` | `page_name`, `page_url`, `last_synced_at`, `revision_id` |
| `UpdatePage` | `page_name`, `url`, `revision_id`, `links` (from `read_update_page`) |
| `SyncStatus` | `queue_stats`, `update_pages` (by page name) |

---

## Exceptions

All exceptions inherit from `DDOSyncError`.

| Exception | When raised |
|---|---|
| `DDOSyncError` | Base class |
| `UpdatePageError` | The named-items index or an update page could not be read, or the index links to no update page |
| `QueueDbError` | Queue database operation failed |
| `QueueSchemaError` | Queue schema could not be applied, or the file is from an older schema |

Extraction failures (`item_extractor.ExtractionError`) and Page Store `FetchError`s do not escape `process_queue`: the item is marked failed. `page_store.RunStoppedError` does escape it, and the item stays pending.
