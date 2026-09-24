"""Sync all DDO named-item update pages to a local SQLite database.

Discovers every ``Update_<N>_named_items`` page on DDO Wiki, registers
them with the sync engine, and runs a full sync cycle that:

  1. Checks each update page's last-modified timestamp via the MediaWiki API.
  2. Re-fetches and re-parses any page that has changed since the last sync.
  3. Scrapes each newly discovered (or re-queued) item page.
  4. Normalizes the scraped HTML into a structured DDOItem.
  5. Upserts the item into the local SQLite loot database.

Databases:
    data/loot.db   — normalized item data (managed by item_db)
    data/queue.db  — scrape queue and update-page sync state (managed by ddo_sync)

Usage:
    python scripts/sync_named_items.py [--limit N] [--dry-run]

Options:
    --limit N    Process at most N queue items per run (default: unlimited).
    --dry-run    Discover and register pages but do not fetch or write anything.
    --verbose    Enable DEBUG-level log output.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

# ── Ensure src/ is importable when running without editable install ───────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ddowiki_scraper import WikiFetcher, WikiFetcherConfig
from item_db import ItemRepository
from item_normalizer import ItemNormalizer

from ddo_sync import DDOSyncer, QueueRepository, UpdatePageDiscoverer

# ── Default database paths ────────────────────────────────────────────────────
DATA_DIR = ROOT / "data"
LOOT_DB = DATA_DIR / "loot.db"
QUEUE_DB = DATA_DIR / "queue.db"


def _configure_logging(verbose: bool) -> None:
    logger.remove()  # drop the default handler
    level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        colorize=True,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync all DDO named-item update pages to a local database."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of queue items to process (default: unlimited).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and register pages only — do not fetch items or write to the DB.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level log output.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _configure_logging(args.verbose)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Discover all update pages ─────────────────────────────────────
    logger.info("Discovering DDO named-item update pages…")
    discoverer = UpdatePageDiscoverer()
    try:
        page_names = discoverer.discover()
    except Exception as exc:
        logger.error(f"Page discovery failed: {exc}")
        return 1

    if not page_names:
        logger.warning("No update pages found — nothing to sync.")
        return 0

    logger.info(f"Found {len(page_names)} update page(s):")
    for name in page_names:
        logger.info(f"  {name}")

    if args.dry_run:
        logger.info("Dry-run mode: stopping before any fetching or DB writes.")
        return 0

    # ── Step 2: Wire up components and run the sync ───────────────────────────
    fetcher_config = WikiFetcherConfig(
        rate_limit_delay=2.5,   # be polite to ddowiki.com
        max_retries=3,
        timeout=30,
    )
    normalizer = ItemNormalizer()

    with (
        WikiFetcher(fetcher_config) as fetcher,
        ItemRepository(str(LOOT_DB)) as item_repo,
        QueueRepository(str(QUEUE_DB)) as queue_repo,
    ):
        syncer = DDOSyncer(
            fetcher=fetcher,
            normalizer=normalizer,
            item_repo=item_repo,
            queue_repo=queue_repo,
            max_retries=3,
        )

        # Register every discovered page (idempotent — safe to re-run)
        for name in page_names:
            syncer.register_update_page(name)

        # ── Step 3: Run the sync cycle ────────────────────────────────────────
        logger.info("Starting sync cycle…")
        status = syncer.sync_all() if args.limit is None else _sync_with_limit(syncer, args.limit)

        # ── Step 4: Print summary ─────────────────────────────────────────────
        q = status.queue_stats
        logger.info("─" * 50)
        logger.info("Sync complete")
        logger.info(f"  Queue — pending: {q.pending}  complete: {q.complete}  "
                    f"failed: {q.failed}  skipped: {q.skipped}  total: {q.total}")

        stale = [p for p in status.update_pages.values() if p.needs_resync]
        if stale:
            logger.warning(f"  {len(stale)} page(s) still marked needs_resync (check failed items):")
            for p in stale:
                logger.warning(f"    {p.page_name}")
        else:
            logger.info("  All update pages are up to date.")

        logger.info(f"  Loot DB:  {LOOT_DB}")
        logger.info(f"  Queue DB: {QUEUE_DB}")

    return 0 if status.queue_stats.failed == 0 else 2


def _sync_with_limit(syncer: DDOSyncer, limit: int):
    """Run the update-page refresh then process only `limit` queue items."""
    # Reset failed items, refresh timestamps, re-parse stale pages
    from ddo_sync.exceptions import UpdatePageError
    from ddo_sync.queue_db import QueueRepository as _QR  # already imported above

    reset_count = syncer._queue_repo.reset_failed_to_pending(syncer._max_retries)
    if reset_count:
        logger.info(f"Reset {reset_count} failed item(s) to pending")

    for page_status in syncer._queue_repo.list_update_pages():
        syncer._refresh_wiki_timestamp(page_status.page_name)
        updated = syncer._queue_repo.get_update_page_status(page_status.page_name)
        if updated and updated.needs_resync:
            logger.info(f"Re-syncing {page_status.page_name!r}…")
            try:
                syncer.sync_update_page(page_status.page_name)
            except UpdatePageError as exc:
                logger.error(f"Failed to sync {page_status.page_name!r}: {exc}")

    syncer.process_queue(limit=limit)
    return syncer.get_status()


if __name__ == "__main__":
    sys.exit(main())
