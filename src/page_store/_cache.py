"""On-disk layout of the Page Store: one HTML file per page plus ``index.json``.

Internal to the Page Store module. ``index.json`` maps each page title to
``{url, file, fetched_at, via}``; it only says what is cached. The crawl ledger is the
SQLite queue in ``ddo_sync``.

Filenames are a readable slug of the page title plus the first 8 hex digits of the
title's SHA-1, so titles that slug alike (``Item:A'B`` and ``Item:A B``) never collide.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

INDEX_NAME = "index.json"
_SLUG_MAX = 80

WIKI_HOST = "ddowiki.com"
_PAGE_PREFIX = "/page/"


def page_title(url: str) -> str:
    """Wiki page title of a ``/page/`` URL, e.g. ``Item:Breaker of Bodies``.

    Raises:
        ValueError: *url* is not a ``https://ddowiki.com/page/...`` article URL. ADR 0006
            allows ``/page/`` reads only: never ``/api.php``, ``Special:`` or a query.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.netloc != WIKI_HOST:
        raise ValueError(f"not a {WIKI_HOST} URL: {url!r}")
    if not parsed.path.startswith(_PAGE_PREFIX) or parsed.query:
        raise ValueError(f"only /page/ article URLs may be fetched: {url!r}")
    title = unquote(parsed.path[len(_PAGE_PREFIX) :]).replace("_", " ").strip()
    if not title or title.casefold().startswith("special:"):
        raise ValueError(f"only /page/ article URLs may be fetched: {url!r}")
    return title


def filename_for(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:_SLUG_MAX] or "page"
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}.html"


class CacheIndex:
    """``index.json`` plus the page files beside it, in one directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._path = root / INDEX_NAME
        self._entries: dict[str, dict[str, Any]] | None = None

    def lookup(self, title: str) -> dict[str, Any] | None:
        entry = self._load().get(title)
        if entry is None or not (self.root / entry["file"]).exists():
            return None
        return entry

    def entries(self) -> Iterator[tuple[str, dict[str, Any]]]:
        yield from sorted(self._load().items())

    def read(self, entry: dict[str, Any]) -> str:
        return (self.root / entry["file"]).read_text(encoding="utf-8")

    def store(self, title: str, html: str, entry: dict[str, Any]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        entry = {**entry, "file": filename_for(title)}
        _atomic_write(self.root / entry["file"], html)
        entries = self._load()
        entries[title] = entry
        _atomic_write(
            self._path,
            json.dumps(entries, indent=2, sort_keys=True, ensure_ascii=False),
        )
        return entry

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is None:
            if self._path.exists():
                self._entries = json.loads(self._path.read_text(encoding="utf-8"))
            else:
                self._entries = {}
        return self._entries


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
