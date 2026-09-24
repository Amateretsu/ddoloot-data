"""Classify the entries of an Enchantments/Enhancements list using enchantments.yaml."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import Tag

from item_extractor.config import Config


@dataclass
class Classified:
    """Result of classifying one list entry."""

    kind: str  # effect | set_bonus | hint
    rule_id: str
    data: dict[str, Any]
    unclassified: bool = False


@dataclass
class EntryView:
    """The parts of one <li> the rules need."""

    text: str
    tooltip: str | None
    hrefs: list[str]
    children: list[str] = field(default_factory=list)
    #: <li> texts inside the tooltip (set bonus lists live here)
    tooltip_items: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def read_entry(li: Tag) -> EntryView:
    """Split an <li> into visible text, tooltip text, anchor hrefs and nested entries."""
    li = copy.copy(li)
    tooltip_el = li.find("span", class_="tooltip")
    tooltip = _clean(tooltip_el.get_text(" ", strip=True)) if tooltip_el else None
    hrefs = [a.get("href", "") for a in li.find_all("a")]
    tooltip_items = (
        [_clean(t.get_text(" ", strip=True)) for t in tooltip_el.find_all("li")]
        if tooltip_el
        else []
    )
    for junk in li.find_all("span", class_=["tooltip", "mw-valign-super"]):
        junk.decompose()
    children = [_clean(c.get_text(" ", strip=True)) for c in li.find_all("li")]
    for nested in li.find_all("ul"):
        nested.decompose()
    return EntryView(
        text=_clean(li.get_text(" ", strip=True)),
        tooltip=tooltip,
        hrefs=hrefs,
        children=children,
        tooltip_items=tooltip_items,
    )


def _bonus_type(
    cfg: Config, entry: EntryView, rule: dict[str, Any], groups: dict[str, str]
) -> str | None:
    bt = cfg.enchantments["bonus_type"]
    if groups.get("btype"):
        return groups["btype"].lower()
    for href in entry.hrefs:
        m = re.search(bt["link_pattern"], href)
        if m:
            return m.group(1).lower()
    source = groups.get("text") if rule.get("bonus_type_from") == "text" else None
    if source:
        m = re.search(bt["text_pattern"], source)
        if m:
            return m.group(1).lower()
    if entry.tooltip and "tooltip_pattern" in bt:
        m = re.search(bt["tooltip_pattern"], entry.tooltip)
        if m:
            return m.group(1).lower()
    return None


def _number(raw: str | None, sign: str | None) -> float | int | None:
    if raw is None:
        return None
    value: float | int = float(raw) if "." in raw else int(raw)
    return -value if sign == "-" else value


def classify(cfg: Config, entry: EntryView) -> Classified:
    """Return the first matching rule's interpretation of one list entry."""
    roman = cfg.mappings["roman"]
    for rule in cfg.enchantments["rules"]:
        when = rule.get("when", {})
        if "has_children" in when and when["has_children"] != bool(entry.children):
            continue
        if "tooltip_items_match" in when and not any(
            re.match(when["tooltip_items_match"], t) for t in entry.tooltip_items
        ):
            continue
        m = re.match(rule["pattern"], entry.text)
        if not m:
            continue
        g = {k: v for k, v in m.groupdict().items() if v is not None}
        for key in rule.get("lowercase", []):
            if key in g:
                g[key] = g[key].lower()
        kind = rule["kind"]
        if kind == "set":
            bonuses = []
            for text in entry.tooltip_items:
                bm = re.match(rule["item_pattern"], text)
                if bm:
                    bg = bm.groupdict()
                    bonuses.append(
                        {
                            "pieces": int(bg["pieces"]),
                            "text": bg["text"],
                            "bonus_type": _bonus_type(cfg, entry, rule, bg),
                        }
                    )
            return Classified(
                "set", rule["id"], {"name": g["name"], "bonuses": bonuses}
            )
        if kind == "hint":
            hint_kind = rule["hint_kind"].format(**g)
            data = {
                "kind": hint_kind,
                "raw": entry.text,
                **{k: v for k, v in g.items() if k != "hint" and v},
            }
            if entry.children:
                data["children"] = entry.children
            return Classified("hint", rule["id"], data)
        if kind == "set_bonus":
            data = {
                "pieces": int(g["pieces"]),
                "text": g["text"],
                "bonus_type": _bonus_type(cfg, entry, rule, g),
            }
            return Classified("set_bonus", rule["id"], data)
        value_kind = rule.get("value_kind")
        value: float | int | None = None
        if "tier" in g:
            value, value_kind = roman.get(g["tier"]), "tier"
        elif "value" in g:
            value = _number(g["value"], g.get("sign"))
            if g.get("pct"):
                value_kind = "percent"
        data = {
            "name": _clean(g["name"]),
            "value": value,
            "value_kind": value_kind if value is not None else None,
            "bonus_type": _bonus_type(cfg, entry, rule, g),
            "tooltip": entry.tooltip,
        }
        unclassified = bool(rule.get("fallback")) and bool(re.search(r"\d", entry.text))
        return Classified("effect", rule["id"], data, unclassified)
    raise ValueError(
        f"no rule matched {entry.text!r}; the config needs a fallback rule"
    )
