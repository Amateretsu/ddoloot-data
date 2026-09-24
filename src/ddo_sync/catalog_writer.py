"""The committed catalog layout (ADR 0006): the adapter that writes it and the check on it.

:class:`CatalogWriter` is the production adapter behind
:class:`~ddo_sync.protocols.ScrapedItemWriterProtocol`. It writes one committed file per
Named Item and one gitignored review report per update::

    <items_dir>/<update>/<category>/<uuid>-<slug>.json   {"id": <uuid>, **ScrapedItem}
    <report_dir>/<update>/report.jsonl                   one line per page

:func:`check_catalog` is the integrity check over a ``catalog-src`` directory, run in CI.
Both own the same layout rules, which live only in this module:

* ``<uuid>`` comes from the UUID registry (:class:`catalog_registry.Registry`), keyed by
  the item's ``wiki.page_id``. An item whose page ID is null gets no UUID and no file; its
  report line carries a ``wiki.page_id`` entry in ``extraction_errors`` and the run goes
  on.
* ``<update>`` is the introduced-in update, spelled like the report folders:
  ``update-<N>`` for ``Update_<N>_named_items``, else ``unknown``. An item listed on
  several update pages keeps one file under the lowest ``N``; ``unknown`` loses to any
  number.
* ``<category>`` is the Scraped Item's ``category`` when it is weapon, armor, shield,
  jewelry or clothing, else ``other``.
* ``<slug>`` is the item name lowercased, every run of characters other than ``a-z`` and
  ``0-9`` replaced by ``-``, trimmed of ``-``, cut to 60 characters, and ``item`` when
  empty.
* There is exactly one file per UUID. A write finds the UUID's existing file
  (``*/*/<uuid>-*.json``) and moves it when the update, category or slug changes, so git
  records a rename.
* A file is JSON with ``id`` first, 2-space indent, ``ensure_ascii=False`` and a trailing
  newline. Nothing time-dependent is written, so only a wiki or extractor change alters it.

``report.jsonl`` is filed under the update page the item was queued from, and keeps
exactly one line per page: writing a page again replaces its line, across runs and
``--limit`` batches. Two spellings of one page URL (``%27`` or ``'``) are the same page.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Union
from urllib.parse import unquote

from catalog_registry import Registry
from ddo_sync.discovery import update_slug
from item_extractor import ScrapedItem

_CATEGORIES = ("weapon", "armor", "shield", "jewelry", "clothing")
_SLUG_MAX = 60
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_FILE_RE = re.compile(rf"^({_UUID})-(.+)\.json$")
_UPDATE_DIR_RE = re.compile(r"^update-(\d+)$")
_NULL_PAGE_ID = "null page ID: no UUID, no item file written"


class CatalogWriter:
    """Write Scraped Items into the committed ``catalog-src/items`` layout.

    Args:
        registry: The UUID registry; :meth:`write` mints IDs in it. The caller saves it.
        items_dir: Root of the committed item files; folders are created on first write.
        report_dir: Root of the gitignored per-update ``report.jsonl`` files.
    """

    def __init__(
        self,
        registry: Registry,
        items_dir: Path = Path("catalog-src/items"),
        report_dir: Path = Path("cache/extracted"),
    ) -> None:
        self.registry = registry
        self.items_dir = items_dir
        self.report_dir = report_dir

    def write(self, item: ScrapedItem, report: dict[str, Any]) -> None:
        """Write *item*'s file and replace its line in the update's ``report.jsonl``.

        The report line is ``{name, url, update_page, template, unmapped_rows,
        unclassified_effects, ..., extraction_errors, warnings}``: the extractor's report
        plus the item's ``extraction_errors`` and a ``warnings`` list (empty when the
        report has none). A null page ID adds a ``wiki.page_id`` extraction error and
        writes no item file.
        """
        errors = dict(item.extraction_errors)
        if item.wiki.page_id is None:
            errors["wiki.page_id"] = _NULL_PAGE_ID
        else:
            update = update_slug(report.get("update_page"))
            self._write_item(item, item.wiki.page_id, update)
        self._write_report_line(item, report, errors)

    def _write_item(self, item: ScrapedItem, page_id: int, update: str) -> None:
        named_item_id = self.registry.id_for(
            page_id, item.wiki.title or _url_title(item.wiki.url)
        )
        existing = sorted(self.items_dir.glob(f"*/*/{named_item_id}-*.json"))
        update = min([update, *(p.parent.parent.name for p in existing)], key=_rank)
        target = (
            self.items_dir
            / update
            / _category_dir(item.category)
            / f"{named_item_id}-{_slug(item.name)}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(target, _render(named_item_id, item))
        for old in existing:
            if old != target:
                old.unlink()
                _remove_empty_dirs(old.parent, self.items_dir)

    def _write_report_line(
        self, item: ScrapedItem, report: dict[str, Any], errors: dict[str, str]
    ) -> None:
        folder = self.report_dir / update_slug(report.get("update_page"))
        folder.mkdir(parents=True, exist_ok=True)
        url = item.wiki.url
        key = _page_key(url)
        line = {
            "name": item.name,
            "url": url,
            "update_page": None,
            **report,
            "extraction_errors": errors,
            "warnings": report.get("warnings", []),
        }
        report_path = folder / "report.jsonl"
        kept = []
        if report_path.exists():
            for raw in report_path.read_text(encoding="utf-8").splitlines():
                if raw.strip() and _page_key(json.loads(raw).get("url", "")) != key:
                    kept.append(raw)
        kept.append(json.dumps(line, ensure_ascii=False))
        _write_atomic(report_path, "\n".join(kept) + "\n")


# ── Layout rules ──────────────────────────────────────────────────────────────


def _slug(name: str | None) -> str:
    """Filename slug for an item name: ``"Sword of Shadow!"`` -> ``sword-of-shadow``."""
    # ASCII only: accented letters become "-" runs too, keeping filenames portable.
    text = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return text[:_SLUG_MAX].strip("-") or "item"


def _category_dir(category: str | None) -> str:
    """The ``<category>`` folder: the Scraped Item's category if listed, else ``other``."""
    return category if category in _CATEGORIES else "other"


def _render(named_item_id: str, item: ScrapedItem) -> str:
    """The exact text of an item file: ``{"id", **ScrapedItem}``, indented, newline-ended."""
    content = {"id": named_item_id, **item.model_dump(mode="json")}
    return json.dumps(content, indent=2, ensure_ascii=False) + "\n"


def _rank(update: str) -> float:
    """Sort key for ``<update>`` folders: lower ``N`` first, ``unknown`` (or junk) last."""
    match = _UPDATE_DIR_RE.match(update)
    return int(match.group(1)) if match else float("inf")


# ── Integrity check ───────────────────────────────────────────────────────────


def check_catalog(catalog_src: Union[str, os.PathLike]) -> list[str]:
    """Check a ``catalog-src`` directory against the layout rules; return the problems.

    Reads ``<catalog_src>/registry.jsonl`` and every ``*.json`` under
    ``<catalog_src>/items``. Each item file must:

    * sit at ``items/<update>/<category>/<uuid>-<slug>.json``, with ``<update>`` spelled
      ``update-<N>`` or ``unknown``, and ``<category>`` and ``<slug>`` following the rules
      above for the file's own ``category`` and ``name``;
    * hold ``{"id": <uuid>, **ScrapedItem}`` that validates, with ``id`` equal to the
      filename's UUID, and be written exactly as :class:`CatalogWriter` writes it;
    * have its ``id`` in the registry with the same ``page_id`` as ``wiki.page_id``;
    * be the only file for its UUID.

    Returns:
        One human-readable line per problem, each starting with the offending path; an
        empty list when the directory is sound (including when it holds no item files).
    """
    root = Path(catalog_src)
    problems: list[str] = []
    page_ids = _registry_page_ids(root / "registry.jsonl", problems)
    items_dir = root / "items"
    seen: dict[str, list[Path]] = {}
    for path in sorted(items_dir.rglob("*.json")) if items_dir.is_dir() else []:
        rel = path.relative_to(root)
        for message in _check_item_file(path, items_dir, page_ids, seen):
            problems.append(f"{rel}: {message}")
    for named_item_id, paths in sorted(seen.items()):
        if len(paths) > 1:
            listed = ", ".join(str(p.relative_to(root)) for p in paths)
            problems.append(
                f"{named_item_id}: {len(paths)} files share this id: {listed}"
            )
    return problems


def _registry_page_ids(path: Path, problems: list[str]) -> dict[str, int]:
    try:
        Registry.load(path)
    except ValueError as exc:
        problems.append(f"{path.name}: {exc}")
        return {}
    if not path.exists():
        return {}
    entries = (json.loads(raw) for raw in path.read_text("utf-8").splitlines() if raw)
    return {entry["id"]: entry["page_id"] for entry in entries}


def _check_item_file(
    path: Path,
    items_dir: Path,
    page_ids: dict[str, int],
    seen: dict[str, list[Path]],
) -> list[str]:
    parts = path.relative_to(items_dir).parts
    match = _FILE_RE.match(path.name)
    if len(parts) != 3 or match is None:
        return ["not at items/<update>/<category>/<uuid>-<slug>.json"]
    update, category, _ = parts
    file_id, file_slug = match.groups()
    seen.setdefault(file_id, []).append(path)
    text = path.read_text(encoding="utf-8")
    try:
        content = json.loads(text)
        if not isinstance(content, dict) or next(iter(content), None) != "id":
            return ['not a JSON object with "id" as its first key']
        named_item_id = content.pop("id")
        item = ScrapedItem.model_validate(content)
    except ValueError as exc:
        return [f"not {{id}} + a valid Scraped Item: {exc}"]

    problems = []
    if named_item_id != file_id:
        problems.append(f"id {named_item_id!r} does not match the filename")
    if text != _render(named_item_id, item):
        problems.append("not written in the canonical form (field order, indent, ...)")
    if named_item_id not in page_ids:
        problems.append(f"id {named_item_id!r} is not in the registry")
    elif page_ids[named_item_id] != item.wiki.page_id:
        problems.append(
            f"registry page_id {page_ids[named_item_id]} != wiki.page_id "
            f"{item.wiki.page_id}"
        )
    if update != "unknown" and not _UPDATE_DIR_RE.match(update):
        problems.append(f"update folder {update!r} is not update-<N> or unknown")
    if category != _category_dir(item.category):
        problems.append(
            f"category folder {category!r} should be {_category_dir(item.category)!r}"
        )
    if file_slug != _slug(item.name):
        problems.append(f"slug {file_slug!r} should be {_slug(item.name)!r}")
    return problems


# ── Helpers ───────────────────────────────────────────────────────────────────


def _page_key(url: str) -> str:
    """One key per wiki page, whichever spelling of its URL (``%27`` or ``'``)."""
    return unquote(url.rsplit("/page/", maxsplit=1)[-1]).replace(" ", "_")


def _url_title(url: str) -> str:
    return unquote(url.rsplit("/page/", maxsplit=1)[-1]).replace("_", " ")


def _remove_empty_dirs(folder: Path, stop: Path) -> None:
    """Remove *folder* and its parents up to (not including) *stop* while they are empty."""
    while folder != stop and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
