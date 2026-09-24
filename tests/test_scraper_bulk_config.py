"""``config/scraper-bulk.yaml`` is ``config/scraper.yaml`` plus the two bulk-run settings,
and nothing else, so the two files cannot drift apart."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from page_store import load_scraper_config

CONFIG = Path(__file__).resolve().parents[1] / "config"
BASE = CONFIG / "scraper.yaml"
BULK = CONFIG / "scraper-bulk.yaml"


def _raw(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_bulk_config_differs_only_in_retries_and_challenges():
    base, bulk = _raw(BASE), _raw(BULK)
    assert bulk["max_retries"] == 0
    assert bulk["browser"]["consecutive_challenges"] == 1
    expected = copy.deepcopy(base)
    expected["max_retries"] = 0
    expected["browser"]["consecutive_challenges"] = 1
    assert bulk == expected


def test_bulk_config_loads_and_uses_the_repo_page_store():
    base, bulk = load_scraper_config(BASE), load_scraper_config(BULK)
    assert bulk.cache_dir == (CONFIG.parent / "cache" / "pages").resolve()
    assert bulk.model_dump(exclude={"max_retries", "browser"}) == base.model_dump(
        exclude={"max_retries", "browser"}
    )
    assert bulk.browser.model_dump(
        exclude={"consecutive_challenges"}
    ) == base.browser.model_dump(exclude={"consecutive_challenges"})
