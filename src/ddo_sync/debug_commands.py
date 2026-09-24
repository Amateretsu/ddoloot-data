"""Debug and inspection commands for DDOLoot.

Provides the ``normalize_item`` helper used by the ``--item`` CLI flag to
fetch, normalize, and optionally upsert a single named item.  Kept separate
from :mod:`ddo_sync.cli` so it can be called programmatically in tests and
REPL sessions without importing the full CLI stack.

Example:
    >>> from ddo_sync.debug_commands import normalize_item
    >>> normalize_item("Mantle of the Worldshaper", upsert=False)
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ddowiki_scraper import WikiFetcher, WikiFetcherConfig
from ddowiki_scraper.exceptions import FetchError, RobotsTxtError
from item_db import ItemRepository
from item_normalizer import ItemNormalizer

WIKI_URL_BASE = "https://ddowiki.com/page/Item:"


def normalize_item(item_name: str, upsert: bool, loot_db: Path) -> None:
    """Fetch, normalize, and optionally persist a single DDO item.

    Prints a summary of the parsed fields to stdout for quick inspection.

    Args:
        item_name: Display name of the DDO item, e.g. ``"Mantle of the Worldshaper"``.
        upsert:    If ``True``, call ``upsert()``; otherwise call ``save()``.
        loot_db:   Path to the loot SQLite database file.
    """
    logger.debug(f"Normalizing {item_name!r}")

    config = WikiFetcherConfig(
        rate_limit_delay=2.5,
        respect_robots_txt=True,
    )

    html = ""
    with WikiFetcher(config) as fetcher:
        try:
            html = fetcher.fetch_item_page(item_name)
            print(f"Fetched {len(html):,} bytes")  # noqa: T201
            print(f"Page title found: {item_name in html}")  # noqa: T201
        except RobotsTxtError as exc:
            print(f"Blocked by robots.txt: {exc}")  # noqa: T201
            return
        except FetchError as exc:
            print(f"Fetch failed (HTTP {exc.status_code}): {exc}")  # noqa: T201
            return

    normalizer = ItemNormalizer()
    item = normalizer.normalize(html, wiki_url=f"{WIKI_URL_BASE}{item_name}")

    logger.debug(f"Name          : {item.name}")
    logger.debug(f"Slot          : {item.slot}")
    logger.debug(f"Minimum level : {item.minimum_level}  (int, not string)")
    logger.debug(f"Binding       : {item.binding}")
    logger.debug(f"Material      : {item.material}")
    logger.debug(f"Hardness      : {item.hardness}")
    logger.debug(f"Durability    : {item.durability}")
    logger.debug(f"Weight        : {item.weight} lbs")
    logger.debug(f"Flavor text   : {item.flavor_text!r}")

    print(f"\nEnchantments ({len(item.enchantments)}):")  # noqa: T201
    for enc in item.enchantments:
        value_str = f" {enc.value}" if enc.value else ""
        print(f"  - {enc.name}{value_str}")  # noqa: T201

    if item.named_set:
        print(f"\nNamed set: {item.named_set.name}")  # noqa: T201

    if item.source:
        print(f"Source quests: {item.source.quests}")  # noqa: T201

    with ItemRepository(str(loot_db)) as item_repo:
        if upsert:
            item_repo.upsert(item)
            logger.info(f"Upserted item: {item.name!r}")
        else:
            item_repo.save(item)
            logger.info(f"Saved item: {item.name!r}")
