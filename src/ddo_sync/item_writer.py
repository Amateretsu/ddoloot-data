"""JSON adapter behind :class:`~ddo_sync.protocols.ScrapedItemWriterProtocol`.

Results layout, one folder per update (a local, gitignored working copy; the committed
``catalog-src`` layout with UUIDs is a later stage)::

    <out_dir>/<update>/<page-slug>.json   one Scraped Item, every field present
    <out_dir>/<update>/report.jsonl       one line per page

``<update>`` is ``update-8`` style, derived from the report's ``update_page``
(``Update_8_named_items`` -> ``update-8``) and ``unknown`` when absent. ``<page-slug>`` is
the URL title with every run of non-alphanumerics replaced by ``_``, e.g.
``Item_Breaker_of_Bodies``.

``report.jsonl`` keeps exactly one line per page URL: writing a page again replaces its
line (in place of appending a duplicate), so the report always describes the JSON files
beside it, however many runs or ``--limit`` batches produced them.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ddo_sync.discovery import update_slug
from item_extractor import ScrapedItem


def _page_slug(url: str) -> str:
    """Readable filename stem for a wiki page URL, e.g. ``Item_Breaker_of_Bodies``."""
    title = unquote(url.rsplit("/page/", maxsplit=1)[-1])
    return re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_") or "page"


class JsonItemWriter:
    """Write Scraped Items as JSON files under per-update folders of one directory.

    Args:
        out_dir: Root results directory; folders are created on first write.
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir

    def write(self, item: ScrapedItem, report: dict[str, Any]) -> None:
        """Write *item* and replace its line in the update's ``report.jsonl``.

        The report line is ``{name, url, update_page, template, unmapped_rows,
        unclassified_effects, ..., extraction_errors, warnings}``: the extractor's report
        plus the item's ``extraction_errors`` and a ``warnings`` list (empty when the
        report has none).
        """
        folder = self.out_dir / update_slug(report.get("update_page"))
        folder.mkdir(parents=True, exist_ok=True)
        url = item.wiki.url
        _write_atomic(
            folder / f"{_page_slug(url)}.json",
            json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False)
            + "\n",
        )
        line = {
            "name": item.name,
            "url": url,
            "update_page": None,
            **report,
            "extraction_errors": item.extraction_errors,
            "warnings": report.get("warnings", []),
        }
        report_path = folder / "report.jsonl"
        kept = []
        if report_path.exists():
            for raw in report_path.read_text(encoding="utf-8").splitlines():
                if raw.strip() and json.loads(raw).get("url") != url:
                    kept.append(raw)
        kept.append(json.dumps(line, ensure_ascii=False))
        _write_atomic(report_path, "\n".join(kept) + "\n")


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
