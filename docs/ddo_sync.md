# ddo_sync

Orchestration layer that discovers DDO Wiki update pages, builds a persistent scrape queue, and drives the full fetch → normalize → store pipeline. Also provides the `ddoloot` CLI command.

---

## Overview

The sync pipeline works in three stages:

1. **Discover** — `UpdatePageDiscoverer` queries the MediaWiki API to find all `Update_<N>_named_items` pages
2. **Queue** — `UpdatePageParser` extracts item links from each page; links are written to `QueueRepository`
3. **Process** — `DDOSyncer` works through the queue: fetches each item, normalizes it, and upserts to `ItemRepository`

Items that fail are retried up to `max_retries` times before being marked permanently failed (until `--reset-failed` is used).

---

## CLI

Install the package with `pip install -e .` to get the `ddoloot` command, or run `python main.py` directly.

```
ddoloot [--status | --discover | --reset-failed]
        [--page PAGE [PAGE …]]
        [--limit N]
        [--rate-limit SECONDS]
        [--verbose]
```

| Flag | Description |
|---|---|
| *(no flags)* | Full sync: discover all update pages, queue items, process queue |
| `--status` | Print database and queue statistics, then exit |
| `--discover` | List discovered update pages without syncing |
| `--reset-failed` | Reset all failed queue items to pending, then exit |
| `--page NAME …` | Sync one or more specific update pages |
| `--limit N` | Cap the number of items processed in this run |
| `--rate-limit SECONDS` | Override inter-request delay (default: 2.5s) |
| `--verbose` | Enable DEBUG-level logging |

**Exit codes:** `0` = success · `1` = fatal error · `2` = completed with failures

### Examples

```bash
# Full sync
ddoloot

# Check what's been synced so far
ddoloot --status

# See which update pages exist without syncing anything
ddoloot --discover

# Sync two specific pages
ddoloot --page "Update_69_named_items" "Update_70_named_items"

# Process at most 20 items (useful during testing)
ddoloot --limit 20

# Retry everything that failed in a previous run
ddoloot --reset-failed

# Slow down requests to be extra polite
ddoloot --rate-limit 5.0
```

---

## DDOSyncer

The main orchestrator. Ties together `WikiFetcher`, `ItemNormalizer`, `ItemRepository`, and `QueueRepository`.

```python
from ddowiki_scraper import WikiFetcher, WikiFetcherConfig
from item_normalizer import ItemNormalizer
from item_db import ItemRepository
from ddo_sync import DDOSyncer, QueueRepository

config = WikiFetcherConfig()
with WikiFetcher(config) as fetcher, \
     ItemRepository("data/loot.db") as item_repo, \
     QueueRepository("data/queue.db") as queue_repo:

    syncer = DDOSyncer(
        fetcher=fetcher,
        normalizer=ItemNormalizer(),
        item_repo=item_repo,
        queue_repo=queue_repo,
        max_retries=3,
    )

    # Discover all update pages and register them
    syncer.discover_and_register_all()

    # Process the full queue
    status = syncer.sync_all()
    print(f"Synced: {status.synced}  Failed: {status.failed}  Pending: {status.pending}")
```

### Registration

#### `register_update_page(page_name: str) → None`

Register a single update page for tracking. The page's HTML is fetched, item links are extracted, and new links are added to the queue. Existing queue entries are not duplicated.

```python
syncer.register_update_page("Update_69_named_items")
```

#### `discover_and_register_all() → list[str]`

Queries the MediaWiki API to find all update pages, then registers each one. Returns the list of discovered page names.

### Syncing

#### `sync_all() → SyncStatus`

Processes every pending item in the queue. Returns a `SyncStatus` snapshot.

#### `sync_update_page(page_name: str) → UpdatePageStatus`

Re-parses one update page to pick up any new items, then processes only those items.

#### `process_queue(limit: Optional[int] = None) → SyncStatus`

Process up to `limit` pending items (or all pending items if `limit` is `None`).

#### `retry_failed_items() → None`

Resets all failed items back to `pending` so they are retried on the next `process_queue` call.

### Status

#### `get_status() → SyncStatus`

Returns a complete snapshot of the current sync state.

#### `get_queue_stats() → QueueStats`

Returns queue counts only (pending / success / failed / total).

---

## UpdatePageDiscoverer

Queries the MediaWiki `allpages` API to find update pages, then filters to those matching `Update_<N>_named_items`.

```python
from ddo_sync import UpdatePageDiscoverer

discoverer = UpdatePageDiscoverer(timeout=15.0)
pages = discoverer.discover()
# ["Update_5_named_items", "Update_6_named_items", ..., "Update_70_named_items"]
```

Results are sorted numerically by update number. The discoverer follows MediaWiki API continuation tokens automatically so the full list is always returned regardless of result-set size.

**Raises:** `WikiApiError` on HTTP failure or malformed API response.

---

## UpdatePageParser

Extracts item links from a rendered update page HTML.

```python
from ddo_sync import UpdatePageParser

parser = UpdatePageParser(base_url="https://ddowiki.com")
links = parser.parse_item_links(html)
# [ItemLink(name="Sword of Shadow", wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow"), ...]
```

- Deduplicates links within the same page.
- Only returns links under the `/page/Item:` path — navigation links and other wiki links are excluded.

---

## QueueRepository

Persistent scrape queue backed by SQLite. Tracks every item link discovered on update pages along with its sync status.

```python
from ddo_sync import QueueRepository, ItemLink

with QueueRepository("data/queue.db") as repo:
    link = ItemLink(name="Sword of Shadow", wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow")
    repo.enqueue(link, page_name="Update_69_named_items")

    pending = repo.get_pending()    # list[QueueItem]
    stats = repo.get_stats()        # QueueStats
```

### Methods

| Method | Description |
|---|---|
| `enqueue(link, page_name)` | Add an item to the queue (no-op if already present) |
| `get_pending() → list[QueueItem]` | All items with status `"pending"` |
| `mark_success(item_id)` | Set status to `"success"` |
| `mark_failed(item_id, error_msg)` | Set status to `"failed"`, increment retry count |
| `reset_failed_items() → int` | Reset all `"failed"` back to `"pending"`, return count |
| `get_stats() → QueueStats` | Summary: total, pending, success, failed counts |

---

## WikiApiClient

Thin wrapper around the MediaWiki Action API, used internally by `UpdatePageDiscoverer`.

```python
from ddo_sync import WikiApiClient

client = WikiApiClient()
page_info = client.get_page_info("Update_69_named_items")
```

**Raises:** `WikiApiError` on HTTP or JSON errors.

---

## Data Models

### ItemLink

```python
@dataclass(frozen=True)
class ItemLink:
    name: str       # "Sword of Shadow"
    wiki_url: str   # "https://ddowiki.com/page/Item:Sword_of_Shadow"
```

### QueueItem

```python
@dataclass(frozen=True)
class QueueItem:
    id: int
    item_name: str
    page_name: str              # Source update page
    wiki_url: str
    status: str                 # "pending" | "success" | "failed"
    retry_count: int
    last_error: Optional[str]
    enqueued_at: datetime
    synced_at: Optional[datetime]
```

### QueueStats

```python
@dataclass(frozen=True)
class QueueStats:
    total: int
    pending: int
    success: int
    failed: int
```

### SyncStatus

```python
@dataclass(frozen=True)
class SyncStatus:
    timestamp: datetime
    total_items: int
    synced: int
    failed: int
    pending: int
    pages_registered: int
```

### UpdatePageStatus

```python
@dataclass(frozen=True)
class UpdatePageStatus:
    page_name: str
    total_items: int
    synced: int
    failed: int
    pending: int
    last_synced_at: Optional[datetime]
```

---

## Exceptions

All exceptions inherit from `DDOSyncError`.

| Exception | When raised |
|---|---|
| `DDOSyncError` | Base class |
| `UpdatePageError` | Failed to fetch or parse an update page |
| `WikiApiError` | MediaWiki API request failed or returned unexpected structure |
| `QueueDbError` | Queue database operation failed |
| `QueueSchemaError` | Queue schema could not be applied |

```python
from ddo_sync import WikiApiError, UpdatePageError

try:
    syncer.register_update_page("Update_69_named_items")
except UpdatePageError as e:
    print(f"Could not register page: {e}")
except WikiApiError as e:
    print(f"API error: {e}")
```
