"""DDOLoot command-line interface: the ``ddoloot`` console script.

Subcommands:

    ddoloot sync                                   # discover update pages, scrape, extract
    ddoloot sync --status                          # queue stats, no sync
    ddoloot sync --discover                        # list pages, no sync
    ddoloot sync --page Update_5_named_items [...] # specific pages only
    ddoloot sync --limit 50                        # cap queue items
    ddoloot sync --refresh                         # refetch pages the Page Store holds
    ddoloot sync --reset-failed                    # retry failures, exit
    ddoloot sample --count 40                      # stratified sample into the Page Store
    ddoloot extract-item "Breaker of Bodies"       # extract one page held in the Page Store
    ddoloot extract-item NAME --html PATH          # extract one saved HTML page

Every wiki page is read through the Page Store (``page_store``), whose acquisition policy
and cache directory come from ``--scraper-config PATH`` (default ``config/scraper.yaml``).
``sync`` and ``sample`` track the crawl in the SQLite queue at ``--queue-db PATH``
(default ``data/queue.db``). ``sync`` writes each Scraped Item to
``cache/extracted/<page-slug>.json`` plus ``cache/extracted/report.jsonl``.
``extract-item`` never touches the network. Add ``--verbose`` to any subcommand for DEBUG
logging.

Exit codes:
    0  complete, no failures
    1  startup or fatal error, or the run was stopped by the acquisition policy
    2  completed with failed items remaining (sync) or failed pages (sample)
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from loguru import logger

from ddo_sync.exceptions import UpdatePageError
from ddo_sync.item_writer import JsonItemWriter
from ddo_sync.models import SyncStatus
from ddo_sync.page_discovery import UpdatePageDiscoverer
from ddo_sync.queue_db import QueueRepository
from ddo_sync.sampler import sample_pages
from ddo_sync.syncer import DDOSyncer
from item_extractor import ExtractionError, extract, load_config
from page_store import (
    CachedPage,
    PageStore,
    RunStoppedError,
    ScraperConfig,
    ScraperConfigError,
    load_scraper_config,
)
from page_store.config import DEFAULT_CONFIG_PATH

# ── Default paths ─────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent  # …/src/ddo_sync → root
DATA_DIR = _ROOT / "data"
QUEUE_DB = DATA_DIR / "queue.db"
CACHE_DIR = _ROOT / "cache"
EXTRACTED_DIR = CACHE_DIR / "extracted"

_ITEM_URL_PREFIX = "https://ddowiki.com/page/Item:"


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level log output.",
    )
    store_opts = argparse.ArgumentParser(add_help=False)
    store_opts.add_argument(
        "--scraper-config",
        type=Path,
        default=None,
        metavar="PATH",
        dest="scraper_config",
        help=f"Scraper policy and Page Store location (default: {DEFAULT_CONFIG_PATH}).",
    )
    queue_opts = argparse.ArgumentParser(add_help=False)
    queue_opts.add_argument(
        "--queue-db",
        type=Path,
        default=None,
        metavar="PATH",
        dest="queue_db",
        help=f"SQLite crawl queue (default: {QUEUE_DB}).",
    )

    p = argparse.ArgumentParser(
        prog="ddoloot",
        description="Scrape DDO Wiki named-item pages and extract Scraped Items.",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sync = sub.add_parser(
        "sync",
        parents=[common, store_opts, queue_opts],
        help="Discover update pages, scrape item pages and extract Scraped Items.",
        description=(
            "Discover named-item update pages, read each queued item page through the "
            f"Page Store and write its Scraped Item JSON under {EXTRACTED_DIR}."
        ),
    )
    mode = sync.add_mutually_exclusive_group()
    mode.add_argument(
        "--status",
        action="store_true",
        help="Print queue statistics then exit.",
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
    sync.add_argument(
        "--page",
        metavar="PAGE_NAME",
        nargs="+",
        dest="pages",
        help=(
            "Sync specific update page(s) instead of auto-discovering all. "
            "Accepts underscores or spaces, e.g. Update_5_named_items."
        ),
    )
    sync.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of queue items to process per run.",
    )
    sync.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch every page this run reads, even if the Page Store holds it.",
    )
    sync.add_argument(
        "--max-retries",
        type=int,
        default=3,
        metavar="N",
        dest="max_retries",
        help="Maximum retry attempts before an item is permanently failed (default: 3).",
    )

    sample = sub.add_parser(
        "sample",
        parents=[common, store_opts, queue_opts],
        help="Read a stratified sample of queued item pages into the Page Store.",
        description=(
            "Pick up to N item pages from the crawl queue, spread across as many update "
            "pages as possible, and read each through the Page Store. Pages it already "
            "holds cost no request."
        ),
    )
    sample.add_argument(
        "--count",
        type=int,
        default=40,
        metavar="N",
        help="Number of item pages to sample (default: 40).",
    )
    sample.add_argument(
        "--seed",
        type=int,
        default=1,
        metavar="N",
        help="Random seed for the pick (default: 1).",
    )

    extract_item = sub.add_parser(
        "extract-item",
        parents=[common, store_opts],
        help="Print one item's Scraped Item JSON and report, without network access.",
        description=(
            "Extract one item page held in the Page Store, or a saved HTML file, and "
            "print the Scraped Item and its report as JSON."
        ),
    )
    extract_item.add_argument("name", help='Item name, e.g. "Breaker of Bodies".')
    extract_item.add_argument(
        "--html",
        type=Path,
        metavar="PATH",
        help="Read the page from this HTML file instead of the Page Store.",
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


def _load_store_config(path: Optional[Path]) -> Optional[ScraperConfig]:
    try:
        return load_scraper_config(path)
    except ScraperConfigError as exc:
        logger.error(str(exc))
        return None


def _cmd_status(queue_db: Path) -> int:
    if not queue_db.exists():
        logger.warning("No queue database found. Run `ddoloot sync` to start a sync.")
        return 0

    with QueueRepository(str(queue_db)) as qr:
        stats = qr.get_queue_stats()
        pages = qr.list_update_pages()

    logger.info("─" * 56)
    logger.info(f"Queue DB : {queue_db}")
    logger.info(f"Items    : {EXTRACTED_DIR}")
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


def _cmd_reset_failed(queue_db: Path) -> int:
    if not queue_db.exists():
        logger.warning("No queue database found — nothing to reset.")
        return 0
    with QueueRepository(str(queue_db)) as qr:
        count = qr.reset_failed_to_pending(max_retries=9999)
    logger.info(f"Reset {count} failed item(s) to pending.")
    return 0


def _cmd_sync(
    page_names: Optional[List[str]],
    limit: Optional[int],
    queue_db: Path,
    scraper_config: Optional[Path],
    refresh: bool = False,
    max_retries: int = 3,
) -> int:
    config = _load_store_config(scraper_config)
    if config is None:
        return 1
    queue_db.parent.mkdir(parents=True, exist_ok=True)

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

    _install_sigint_handler()

    try:
        with (
            PageStore(config) as store,
            QueueRepository(str(queue_db)) as queue_repo,
        ):
            syncer = DDOSyncer(
                page_store=store,
                writer=JsonItemWriter(EXTRACTED_DIR),
                queue_repo=queue_repo,
                max_retries=max_retries,
                refresh=refresh,
            )

            for name in pages:
                syncer.register_update_page(name)

            logger.info("Starting sync cycle…")
            status = _run_sync(syncer, limit)
            _print_summary(status, queue_db)

    except KeyboardInterrupt:
        logger.warning("Interrupted — progress saved to the queue, safe to resume.")
        return 1
    except RunStoppedError as exc:
        logger.error(f"Run stopped: {exc}")
        logger.error("No item was marked failed; rerun to resume from the queue.")
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


def _print_summary(status: SyncStatus, queue_db: Path) -> None:
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

    logger.info(f"  Queue DB : {queue_db}")
    logger.info(f"  Items    : {EXTRACTED_DIR}")


def _install_sigint_handler() -> None:
    def _handler(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handler)


def _cmd_sample(
    count: int, seed: int, queue_db: Path, scraper_config: Optional[Path]
) -> int:
    config = _load_store_config(scraper_config)
    if config is None:
        return 1
    if not queue_db.exists():
        logger.error(f"No queue database at {queue_db}; run `ddoloot sync` first.")
        return 1
    try:
        with (
            PageStore(config) as store,
            QueueRepository(str(queue_db)) as queue_repo,
        ):
            results = sample_pages(queue_repo, store, count, seed)
    except RunStoppedError as exc:
        logger.error(f"Run stopped: {exc}")
        return 1
    failed = [r for r in results if r.error is not None]
    logger.info(
        f"Sampled {len(results)} page(s) into {config.cache_dir}: "
        f"{len(results) - len(failed)} held, {len(failed)} failed."
    )
    return 2 if failed else 0


def _cmd_extract_item(
    name: str, html_path: Optional[Path], scraper_config: Optional[Path]
) -> int:
    if html_path is not None:
        html = html_path.read_text(encoding="utf-8")
        url = _ITEM_URL_PREFIX + quote(name.replace(" ", "_"))
    else:
        config = _load_store_config(scraper_config)
        if config is None:
            return 1
        with PageStore(config) as store:
            page = _find_held_page(store, name)
        if page is None:
            logger.error(
                f"{name!r} is not held in the Page Store ({config.cache_dir}); "
                "pass --html PATH to read a saved page."
            )
            return 1
        html, url = page.html, page.url

    try:
        item, report = extract(html, url, load_config())
    except ExtractionError as exc:
        logger.error(f"Could not extract {name!r}: {exc}")
        return 1
    out = {"item": item.model_dump(mode="json"), "report": report}
    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


def _find_held_page(store: PageStore, name: str) -> Optional[CachedPage]:
    """The held page whose title (``Item:`` prefix optional) is *name*, ignoring case."""
    wanted = name.replace("_", " ").removeprefix("Item:").strip().casefold()
    for page in store.iter_cached():
        if page.title.removeprefix("Item:").strip().casefold() == wanted:
            return page
    return None


# ── Entry point ───────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    if args.command == "extract-item":
        return _cmd_extract_item(args.name, args.html, args.scraper_config)
    queue_db = args.queue_db or QUEUE_DB
    if args.command == "sample":
        return _cmd_sample(args.count, args.seed, queue_db, args.scraper_config)
    if args.status:
        return _cmd_status(queue_db)
    if args.discover:
        return _cmd_discover()
    if args.reset_failed:
        return _cmd_reset_failed(queue_db)
    return _cmd_sync(
        page_names=args.pages,
        limit=args.limit,
        queue_db=queue_db,
        scraper_config=args.scraper_config,
        refresh=args.refresh,
        max_retries=args.max_retries,
    )
