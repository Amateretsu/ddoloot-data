"""DDOLoot command-line interface.

Discovers every named-item update page on DDO Wiki, scrapes each item,
normalizes the data, and writes it to two local SQLite databases:

    data/loot.db   — structured item data (queried by the front end)
    data/queue.db  — scrape queue and sync-state tracking (internal)

Usage:
    python main.py                                          # full sync
    python main.py --status                                 # DB stats, no sync
    python main.py --discover                               # list pages, no sync
    python main.py --page Update_5_named_items [Update_6…] # specific pages only
    python main.py --limit 50                               # cap queue items
    python main.py --reset-failed                           # retry failures, exit
    python main.py --verbose                                # DEBUG logging

Exit codes:
    0  complete, no failures
    1  startup or fatal error
    2  completed with failed items remaining
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from typing import List, Optional

from loguru import logger

from ddo_sync.debug_commands import normalize_item
from ddo_sync.exceptions import UpdatePageError
from ddo_sync.models import SyncStatus
from ddo_sync.page_discovery import UpdatePageDiscoverer
from ddo_sync.queue_db import QueueRepository
from ddo_sync.syncer import DDOSyncer
from ddowiki_scraper import WikiFetcher, WikiFetcherConfig
from item_db import ItemRepository
from item_normalizer import ItemNormalizer

# ── Default database paths ────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent  # …/src/ddo_sync → root
DATA_DIR = _ROOT / "data"
LOOT_DB = DATA_DIR / "loot.db"
QUEUE_DB = DATA_DIR / "queue.db"


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ddoloot",
        description="Scrape DDO Wiki named-item pages and populate a local loot database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--status",
        action="store_true",
        help="Print database and queue statistics then exit.",
    )
    mode.add_argument(
        "--discover",
        action="store_true",
        help="List all discoverable update pages then exit (no scraping).",
    )
    mode.add_argument(
        "--reset-failed",
        action="store_true",
        dest="reset_failed",
        help="Reset all failed queue items to pending then exit.",
    )

    p.add_argument(
        "--page",
        metavar="PAGE_NAME",
        nargs="+",
        dest="pages",
        help=(
            "Sync specific update page(s) instead of auto-discovering all. "
            "Accepts underscores or spaces, e.g. Update_5_named_items."
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of queue items to process per run.",
    )
    p.add_argument(
        "--rate-limit",
        type=float,
        default=2.5,
        metavar="SECONDS",
        dest="rate_limit",
        help="Seconds between HTTP requests (default: 2.5, minimum: 1.0).",
    )
    p.add_argument(
        "--item",
        type=str,
        default=None,
        help="Sync an individual item, e.g. Lenses of Opportunity.",
    )
    p.add_argument(
        "--item-override",
        action="store_true",
        help="Overwrite existing data in the db file during an item sync.",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=3,
        metavar="N",
        dest="max_retries",
        help="Maximum retry attempts before an item is permanently failed (default: 3).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level log output.",
    )
    return p


# ── Logging ──────────────────────────────────────────────────────────────────


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "| <level>{level:<8}</level> "
            "| {message}"
        ),
        colorize=True,
    )


# ── Mode handlers ────────────────────────────────────────────────────────────


def _cmd_status() -> int:
    if not QUEUE_DB.exists():
        logger.warning("No queue database found. Run without --status to start a sync.")
        return 0

    with QueueRepository(str(QUEUE_DB)) as qr:
        stats = qr.get_queue_stats()
        pages = qr.list_update_pages()

    logger.info("─" * 56)
    logger.info(f"Loot DB  : {LOOT_DB}")
    logger.info(f"Queue DB : {QUEUE_DB}")
    logger.info("─" * 56)
    logger.info(f"  Total     : {stats.total}")
    logger.info(f"  Complete  : {stats.complete}")
    logger.info(f"  Pending   : {stats.pending}")
    logger.info(f"  Failed    : {stats.failed}")
    logger.info(f"  Skipped   : {stats.skipped}")
    logger.info(f"  In progress: {stats.in_progress}")
    logger.info("─" * 56)
    logger.info(f"  Tracked update pages ({len(pages)}):")
    for p in pages:
        synced = (
            p.last_synced_at.strftime("%Y-%m-%d %H:%M") if p.last_synced_at else "never"
        )
        flag = "  [STALE]" if p.needs_resync else ""
        logger.info(f"    {p.page_name:<42} synced: {synced}{flag}")
    return 0


def _cmd_discover() -> int:
    logger.info("Querying DDO Wiki for named-item update pages…")
    try:
        pages = UpdatePageDiscoverer().discover()
    except Exception as exc:
        logger.error(f"Discovery failed: {exc}")
        return 1

    logger.info(f"Found {len(pages)} update page(s):")
    for name in pages:
        logger.info(f"  {name}")
    return 0


def _cmd_reset_failed() -> int:
    if not QUEUE_DB.exists():
        logger.warning("No queue database found — nothing to reset.")
        return 0
    with QueueRepository(str(QUEUE_DB)) as qr:
        count = qr.reset_failed_to_pending(max_retries=9999)
    logger.info(f"Reset {count} failed item(s) to pending.")
    return 0


def _cmd_sync(
    page_names: Optional[List[str]],
    limit: Optional[int],
    rate_limit: float,
    max_retries: int = 3,
) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Resolve page list ────────────────────────────────────────────────────
    if page_names:
        pages = [n.replace(" ", "_") for n in page_names]
        logger.info(f"Targeting {len(pages)} specific update page(s).")
    else:
        logger.info("Discovering DDO named-item update pages…")
        try:
            pages = UpdatePageDiscoverer().discover()
        except Exception as exc:
            logger.error(f"Page discovery failed: {exc}")
            return 1
        if not pages:
            logger.warning("No update pages found — nothing to sync.")
            return 0
        logger.info(f"Discovered {len(pages)} update page(s).")

    # ── Wire up components ───────────────────────────────────────────────────
    fetcher_config = WikiFetcherConfig(
        rate_limit_delay=max(1.0, rate_limit),
        max_retries=3,
        timeout=30,
    )

    _install_sigint_handler()

    try:
        with (
            WikiFetcher(fetcher_config) as fetcher,
            ItemRepository(str(LOOT_DB)) as item_repo,
            QueueRepository(str(QUEUE_DB)) as queue_repo,
        ):
            syncer = DDOSyncer(
                fetcher=fetcher,
                normalizer=ItemNormalizer(),
                item_repo=item_repo,
                queue_repo=queue_repo,
                max_retries=max_retries,
            )

            for name in pages:
                syncer.register_update_page(name)

            logger.info("Starting sync cycle…")
            status = _run_sync(syncer, limit)
            _print_summary(status)

    except KeyboardInterrupt:
        logger.warning("Interrupted — progress saved to queue.db, safe to resume.")
        return 1
    except Exception as exc:
        logger.error(f"Fatal error: {exc}")
        return 1

    return 0 if status.queue_stats.failed == 0 else 2


def _run_sync(syncer: DDOSyncer, limit: Optional[int]) -> SyncStatus:
    reset = syncer._queue_repo.reset_failed_to_pending(syncer._max_retries)
    if reset:
        logger.info(f"Reset {reset} previously failed item(s) to pending.")

    for page_status in syncer._queue_repo.list_update_pages():
        syncer._refresh_wiki_timestamp(page_status.page_name)
        updated = syncer._queue_repo.get_update_page_status(page_status.page_name)
        if updated and updated.needs_resync:
            logger.info(f"Re-syncing update page: {page_status.page_name!r}")
            try:
                syncer.sync_update_page(page_status.page_name)
            except UpdatePageError as exc:
                logger.error(f"Could not sync {page_status.page_name!r}: {exc}")

    syncer.process_queue(limit=limit)
    return syncer.get_status()


def _print_summary(status: SyncStatus) -> None:
    q = status.queue_stats
    logger.info("─" * 56)
    logger.info("Sync complete")
    logger.info(f"  Complete  : {q.complete}")
    logger.info(f"  Pending   : {q.pending}")
    logger.info(f"  Failed    : {q.failed}")
    logger.info(f"  Total     : {q.total}")

    stale = [p for p in status.update_pages.values() if p.needs_resync]
    if stale:
        logger.warning(f"  {len(stale)} update page(s) still stale:")
        for p in stale:
            logger.warning(f"    {p.page_name}")
    else:
        logger.info("  All update pages are up to date.")

    logger.info(f"  Loot DB  : {LOOT_DB}")
    logger.info(f"  Queue DB : {QUEUE_DB}")


def _install_sigint_handler() -> None:
    def _handler(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handler)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    args = _build_parser().parse_args()
    _configure_logging(args.verbose)

    if args.status:
        return _cmd_status()
    if args.discover:
        return _cmd_discover()
    if args.reset_failed:
        return _cmd_reset_failed()
    if args.item:
        normalize_item(args.item, upsert=args.item_override, loot_db=LOOT_DB)
        return 0

    return _cmd_sync(
        page_names=args.pages,
        limit=args.limit,
        rate_limit=args.rate_limit,
        max_retries=args.max_retries,
    )
