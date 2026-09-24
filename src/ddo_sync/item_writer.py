"""JSON adapter behind :class:`~ddo_sync.protocols.ScrapedItemWriterProtocol`.

Writes each Scraped Item to ``<out_dir>/<page-slug>.json`` (every field present, null when
unknown) and appends one line per page to ``<out_dir>/report.jsonl``. The output is a
local, gitignored working copy; the committed catalog layout is a later stage.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from item_extractor import ScrapedItem


def _page_slug(url: str) -> str:
    """Readable filename stem for a wiki page URL, e.g. ``Item_Breaker_of_Bodies``."""
    title = unquote(url.rsplit("/page/", maxsplit=1)[-1])
    return re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_") or "page"


class JsonItemWriter:
    """Write Scraped Items as JSON files under one directory.

    Args:
        out_dir: Directory to write into; created on first write.
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir

    def write(self, item: ScrapedItem, report: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{_page_slug(item.wiki.url)}.json"
        path.write_text(
            json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        line = {"name": item.name, "url": item.wiki.url, **report}
        with (self.out_dir / "report.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
