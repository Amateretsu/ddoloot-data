"""Config-driven extraction of one wiki item page into a scraped-item dict."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from item_extractor.coercers import COERCERS
from item_extractor.config import Config, normalize_label
from item_extractor.enchantments import classify, read_entry


class ExtractionError(ValueError):
    """The page has no recognisable item infobox."""


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
        score = len(rows) + (100 if labels & cfg.effects_labels else 0)
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


def extract(html: str, url: str, cfg: Config) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract a scraped item and a report of everything the config did not cover.

    Returns:
        (item, report). ``report`` has ``template``, ``unmapped_rows``, ``ignored_rows``,
        ``rule_hits`` and ``unclassified_effects``.

    Raises:
        ExtractionError: no infobox table could be found.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", class_="mw-parser-output")
    if content is None:
        raise ExtractionError("no mw-parser-output content")
    table = _main_table(content, cfg)
    if table is None:
        raise ExtractionError("no infobox table")
    rows = _rows(table)

    meta = _page_meta(html, soup, url)
    name = meta.pop("_name")
    item: dict[str, Any] = {"name": name, "wiki": meta}
    report: dict[str, Any] = {
        "unmapped_rows": {},
        "ignored_rows": [],
        "rule_hits": {},
        "unclassified_effects": [],
    }

    effects, hints = [], []
    for th, td in rows:
        label = normalize_label(th.get_text())
        if label in cfg.effects_labels:
            for li in (td.find("ul") or td).find_all("li", recursive=False):
                entry = read_entry(li)
                if not entry.text:
                    continue
                result = classify(cfg, entry)
                report["rule_hits"][result.rule_id] = (
                    report["rule_hits"].get(result.rule_id, 0) + 1
                )
                if result.unclassified:
                    report["unclassified_effects"].append(entry.text)
                if result.kind == "set":
                    item["named_set"] = result.data
                elif result.kind == "set_bonus":
                    item.setdefault("named_set", {"name": None, "bonuses": []})[
                        "bonuses"
                    ].append(result.data)
                else:
                    {"effect": effects, "hint": hints}[result.kind].append(result.data)
    item["effects"], item["customisation_hints"] = effects, hints

    for span in soup.find_all("span", class_=["tooltip", "sortkey"]):
        span.decompose()

    labels_seen: list[str] = []
    for th, td in rows:
        label = normalize_label(th.get_text())
        labels_seen.append(label)
        if label in cfg.effects_labels:
            continue
        entry_cfg = cfg.label_index.get(label)
        text = re.sub(
            r"\s+", " ", td.get_text(" ", strip=True).replace("\xa0", " ")
        ).strip()
        if entry_cfg is None:
            if label in cfg.ignored_labels or any(
                p.search(label) for p in cfg.ignored_patterns
            ):
                report["ignored_rows"].append(label)
            else:
                report["unmapped_rows"][label] = text[:120]
            continue
        if text.lower() in cfg.null_values:
            continue
        value = COERCERS[entry_cfg["coerce"]](text, td, cfg)
        if entry_cfg.get("spread"):
            for path, v in value.items():
                _set_path(item, path, v)
        elif value is not None:
            _set_path(item, entry_cfg["target"], value)

    _apply_template(item, labels_seen, cfg, report)
    return item, report


def _apply_template(
    item: dict[str, Any], labels: list[str], cfg: Config, report: dict[str, Any]
) -> None:
    label_set = set(labels)
    first = labels[0] if labels else None
    for tpl in cfg.templates["templates"]:
        detect = tpl["detect"]
        if detect.get("default"):
            break
        if "first_label" in detect and first in {
            normalize_label(x) for x in detect["first_label"]
        }:
            break
        if "has_label" in detect and label_set & {
            normalize_label(x) for x in detect["has_label"]
        }:
            break
    report["template"] = tpl["id"]
    item["template"] = tpl["id"]

    def split(value: str, seps: list[str] | str) -> list[str]:
        seps = [seps] if isinstance(seps, str) else seps
        parts = [value]
        for sep in seps:
            parts = [p for part in parts for p in part.split(sep)]
        return [p.strip() for p in parts if p.strip()]

    category = tpl.get("category")
    if "category_from" in tpl:
        src = tpl["category_from"]
        raw = item.get(src["field"])
        if raw:
            head = split(raw, src["split"])[src["index"]]
            category = cfg.templates["category_map"].get(head.lower())
            if category is None:
                report.setdefault("unknown_categories", []).append(head)
    item["category"] = category or "other"

    if "item_type_from" in tpl:
        item["item_type"] = _get_path(item, tpl["item_type_from"])
    elif "item_type_from_split" in tpl:
        src = tpl["item_type_from_split"]
        parts = split(item.get(src["field"]) or "", src["split"])
        item["item_type"] = parts[src["index"]] if len(parts) > src["index"] else None

    if "equip_slots_from" in tpl:
        src = tpl["equip_slots_from"]
        item["equip_slots"] = [
            _slug(s) for s in split(item.pop(src["field"], "") or "", src["split"])
        ]
    else:
        item["equip_slots"] = list(tpl.get("equip_slots", []))
        item.pop("slot", None)
