"""Integrity of the committed catalog source (ADR 0006), run in CI with no network.

Checks the repo's real ``catalog-src/`` with :func:`ddo_sync.check_catalog`: every item
file is ``{"id"} + ScrapedItem``, its ``id`` is in ``registry.jsonl`` with the same
``page_id``, it is the only file for that UUID, and its path follows the update, category
and slug rules. With no item files it passes trivially.
"""

from pathlib import Path

from ddo_sync import check_catalog

CATALOG_SRC = Path(__file__).resolve().parents[1] / "catalog-src"


def test_committed_catalog_src_is_sound():
    assert check_catalog(CATALOG_SRC) == []
