"""Config-driven extraction of one wiki item page into a Scraped Item.

``extract()`` is the module's whole interface: a pure function from page HTML to a
:class:`~item_extractor.scraped_item.ScrapedItem` plus a report of what the config did not
cover. Rows, coercers and templates are implementation behind it. The Effects cell goes
through one internal seam, :func:`~item_extractor.effects.classify_effects`, which owns
Effect routing, the named set merge and reading tooltips. The other cells have their
tooltips stripped one cell at a time, so the Effects cell is never stripped and the order
of the two does not matter.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from item_extractor.coercers import COERCERS, Unparseable
from item_extractor.config import Config, normalize_label
from item_extractor.effects import classify_effects
from item_extractor.scraped_item import ScrapedItem


class ExtractionError(ValueError):
    """The page has no recognisable item infobox."""


class NotEquipmentError(ExtractionError):
    """The page is a wiki item article with no infobox that is filed as a crafting
    ingredient or a consumable: a real item, but not an equippable Named Item. Not a
    failure."""


# Wiki categories of item articles that are not equipment: crafting ingredients, and
# consumables ("Consumables without a type", "Minimum level 1 consumables", "Three-Barrel
# Cove (heroic) consumables"). Only a page with no infobox is checked, so an equipment
# page in one of them still extracts.
_NON_EQUIPMENT_CATEGORY_RE = re.compile(
    r"^(?:Ingredients|Raw ingredients|Consumables without a type|.+ consumables)$"
)


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    node = target
    for key in parents:
        node = node.setdefault(key, {})
    node[leaf] = value


def _get_path(source: dict[str, Any], path: str) -> Any:
    node: Any = source
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _main_table(content: Tag, cfg: Config) -> Tag | None:
    """The infobox: the table with the effects row, else the one with most labelled rows."""
    best, best_score = None, 0
    for table in content.find_all("table"):
        rows = _rows(table)
        labels = {normalize_label(th.get_text()) for th, _ in rows}
        score = len(rows) + (100 if labels & cfg.fields.effects_labels else 0)
        if score > best_score:
            best, best_score = table, score
    return best


def _rows(table: Tag) -> list[tuple[Tag, Tag]]:
    """(th, td) pairs belonging to this table, not to tables nested inside it."""
    pairs = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        th, td = tr.find("th", recursive=False), tr.find("td", recursive=False)
        if th is not None and td is not None:
            pairs.append((th, td))
    return pairs


def _page_meta(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    def grab(key: str) -> int | None:
        m = re.search(rf'"{key}":(\d+)', html)
        return int(m.group(1)) if m else None

    h1 = soup.find("h1", id="firstHeading")
    title = re.sub(r"^Item:", "", h1.get_text(strip=True)).strip() if h1 else None
    return {
        "page_id": grab("wgArticleId"),
        "revision_id": grab("wgRevisionId"),
        "title": h1.get_text(strip=True) if h1 else None,
        "url": url,
        "_name": title,
    }


def _wiki_categories(html: str) -> list[str]:
    """The page's categories, from MediaWiki's ``wgCategories``; empty if unreadable."""
    m = re.search(r'"wgCategories":(\[[^\]]*\])', html)
    try:
        categories = json.loads(m.group(1)) if m else []
    except ValueError:
        return []
    return [c for c in categories if isinstance(c, str)]


def extract(html: str, url: str, cfg: Config) -> tuple[ScrapedItem, dict[str, Any]]:
    """Extract a Scraped Item and a report of everything the config did not cover.

    Returns:
        (item, report). ``report`` has ``template``, ``unmapped_rows``, ``ignored_rows``,
        ``rule_hits``, ``unclassified_effects`` and ``warnings`` (and
        ``unknown_categories`` when an item type's category is not in the category map).
        An Effects entry no rule matches is listed in ``unclassified_effects``; it never
        raises.

    Raises:
        NotEquipmentError: no infobox table, and the page is filed in a crafting
            ingredient category (``Ingredients``, ``Raw ingredients``) or a consumable
            category (``Consumables without a type``, ``… consumables``).
        ExtractionError: no infobox table could be found.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", class_="mw-parser-output")
    if content is None:
        raise ExtractionError("no mw-parser-output content")
    table = _main_table(content, cfg)
    if table is None:
        for category in _wiki_categories(html):
            if _NON_EQUIPMENT_CATEGORY_RE.match(category):
                raise NotEquipmentError(
                    f"not an equippable named item: wiki category {category!r}"
                )
        raise ExtractionError("no infobox table")
    rows = _rows(table)

    meta = _page_meta(html, soup, url)
    name = meta.pop("_name")
    item: dict[str, Any] = {"name": name, "wiki": meta}
    effects_cells = [
        td
        for th, td in rows
        if normalize_label(th.get_text()) in cfg.fields.effects_labels
    ]
    block = (
        classify_effects(effects_cells[0], cfg.enchantments) if effects_cells else None
    )
    warnings: list[str] = []
    if block is not None:
        item["effects"] = block.effects
        item["customisation_hints"] = block.customisation_hints
        item["named_set"] = block.named_set
        warnings.extend(block.warnings)
    if len(effects_cells) > 1:
        warnings.append(
            f"{len(effects_cells)} Effects rows; only the first was classified"
        )
    report: dict[str, Any] = {
        "unmapped_rows": {},
        "ignored_rows": [],
        "rule_hits": dict(block.rule_hits) if block is not None else {},
        "unclassified_effects": list(block.unclassified) if block is not None else [],
        "warnings": warnings,
    }

    errors: dict[str, str] = {}
    labels_seen: list[str] = []
    for th, td in rows:
        label = normalize_label(th.get_text())
        labels_seen.append(label)
        if label in cfg.fields.effects_labels:
            continue
        for span in td.find_all("span", class_=["tooltip", "sortkey"]):
            span.decompose()
        rule = cfg.fields.rule_for(label)
        text = re.sub(
            r"\s+", " ", td.get_text(" ", strip=True).replace("\xa0", " ")
        ).strip()
        if rule is None:
            if cfg.fields.is_ignored(label):
                report["ignored_rows"].append(label)
            else:
                report["unmapped_rows"][label] = text[:120]
            continue
        if text.lower() in cfg.fields.null_values:
            continue
        try:
            value = COERCERS[rule.coerce](text, td, cfg)
        except Unparseable as e:
            errors[rule.error_key] = text
            if rule.spread:
                for path, v in e.partial.items():
                    _set_path(item, path, v)
            continue
        if rule.spread:
            for path, v in value.items():
                _set_path(item, path, v)
        elif value is not None and rule.target is not None:
            _set_path(item, rule.target, value)

    _apply_template(item, labels_seen, _wiki_categories(html), cfg, report)
    for field in cfg.template_inputs:
        item.pop(field, None)
    item["extraction_errors"] = errors
    return ScrapedItem.model_validate(item), report


def _apply_template(
    item: dict[str, Any],
    labels: list[str],
    categories: list[str],
    cfg: Config,
    report: dict[str, Any],
) -> None:
    label_set = set(labels)
    first = labels[0] if labels else None
    for tpl in cfg.templates.templates:
        detect = tpl.detect
        if (
            detect.default
            or first in detect.first_label
            or label_set & detect.has_label
        ):
            break
    report["template"] = tpl.id
    item["template"] = tpl.id

    def split(value: str, seps: list[str]) -> list[str]:
        parts = [value]
        for sep in seps:
            parts = [p for part in parts for p in part.split(sep)]
        return [p.strip() for p in parts if p.strip()]

    category = tpl.category
    if tpl.category_from is not None:
        src = tpl.category_from
        raw = item.get(src.field)
        if raw:
            head = split(raw, src.split)[src.index]
            category = cfg.templates.category_map.get(head.lower())
            if category is None:
                report.setdefault("unknown_categories", []).append(head)
    item["category"] = category or "other"

    if tpl.item_type_from is not None:
        item["item_type"] = _get_path(item, tpl.item_type_from)
    elif tpl.item_type_from_split is not None:
        part = tpl.item_type_from_split
        parts = split(item.get(part.field) or "", part.split)
        item["item_type"] = parts[part.index] if len(parts) > part.index else None
    if item.get("item_type") is None:
        for category, item_type in tpl.item_type_from_category.items():
            if category in categories:
                item["item_type"] = item_type
                break

    if tpl.equip_slots_from is not None:
        every = tpl.equip_slots_from
        item["equip_slots"] = [
            _slug(s) for s in split(item.pop(every.field, "") or "", every.split)
        ]
    else:
        item["equip_slots"] = list(tpl.equip_slots)
