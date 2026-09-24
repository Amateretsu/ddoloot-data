"""Stratified sampling of queued item pages into the Page Store.

Picks up to *count* item pages from the crawl queue, spread across as many update pages
as possible, and reads each through the Page Store so the local copy can be used for
offline extractor work. Pages already held cost no request. Backs ``ddoloot sample``.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional

from loguru import logger

from ddo_sync.models import QueueItem
from ddo_sync.protocols import PageStoreProtocol
from ddo_sync.queue_db import QueueRepository
from page_store import CachedPage, RunStoppedError


@dataclass(frozen=True)
class SampledPage:
    """One sampled queue row and the page the Page Store returned, or why it did not."""

    item: QueueItem
    page: Optional[CachedPage]
    error: Optional[str] = None


def sample_pages(
    queue_repo: QueueRepository,
    page_store: PageStoreProtocol,
    count: int,
    seed: int = 1,
) -> List[SampledPage]:
    """Read a stratified sample of queued item pages through the Page Store.

    Every queued item counts, whatever its status. Update pages are shuffled (by *seed*)
    and visited round-robin, one item from each per round, until *count* items are
    picked, so the sample spans many updates.

    Args:
        queue_repo: The crawl queue to sample from.
        page_store: Where pages are read from (and fetched into when not yet held).
        count: Maximum number of pages to sample.
        seed: Random seed; the same queue and seed give the same sample.

    Returns:
        One :class:`SampledPage` per picked item, in pick order. A page that could not
        be fetched carries ``error`` instead of ``page``.

    Raises:
        page_store.RunStoppedError: The Page Store says stop the run; pages read so far
            stay in the store.
    """
    picked = _pick(queue_repo, count, seed)
    results: List[SampledPage] = []
    for n, item in enumerate(picked, 1):
        try:
            page = page_store.get(item.wiki_url)
        except RunStoppedError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(f"[{n}/{len(picked)}] failed  {item.item_name}: {error}")
            results.append(SampledPage(item=item, page=None, error=error))
            continue
        logger.info(
            f"[{n}/{len(picked)}] held    {item.item_name} ({item.update_page})"
        )
        results.append(SampledPage(item=item, page=page))
    return results


def _pick(queue_repo: QueueRepository, count: int, seed: int) -> List[QueueItem]:
    rng = random.Random(seed)
    by_update: dict[str, List[QueueItem]] = defaultdict(list)
    for update in queue_repo.list_update_pages():
        by_update[update.page_name].extend(
            queue_repo.get_items_for_update_page(update.page_name)
        )
    updates = sorted(by_update)
    rng.shuffle(updates)
    for items in by_update.values():
        rng.shuffle(items)
    picked: List[QueueItem] = []
    while len(picked) < count and any(by_update.values()):
        for update in updates:
            if by_update[update] and len(picked) < count:
                picked.append(by_update[update].pop())
    return picked
