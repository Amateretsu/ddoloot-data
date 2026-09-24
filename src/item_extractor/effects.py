"""Classify an infobox Effects cell (Enchantments/Enhancements) into an EffectsBlock.

This module's interface is :func:`classify_effects`: one Effects ``<td>`` and the typed
``enchantments.yaml`` rules in, one :class:`EffectsBlock` out. It is an internal seam of
the extractor module: ``extract()`` is its only caller and the extractor's tests exercise
it through ``extract()``.

Behind the interface, as implementation:

* **Reading before stripping.** Each ``<li>`` is read from a private copy of the cell, so
  tooltips (which carry Bonus Types and set bonus lists) are seen whatever the caller
  later does to the page.
* **Bug notes.** A wiki bug note at the end of an entry, ``(Bug: …)``, is taken off
  before the rules run and kept in the Effect's ``note``.
* **Routing.** The first matching rule decides whether an entry is an Effect, a
  customisation hint, a named set, or a bare set bonus.
* **Set merge.** A named set row and bare ``N Pieces Equipped`` rows merge into one named
  set whatever order the wiki lists them in: the set row's own bonuses first, then the bare
  rows in page order.
* **Tiered values.** A ``Lesser``/``Greater``/``Superior`` Effect whose entry gives no
  value takes it from its tooltip when the tooltip states exactly one bonus number. Its
  tooltip Bonus Type counts only when every Bonus Type the tooltip names is the same.
* **No-rule entries.** An entry no rule matches is recorded as unclassified; it never
  raises. An entry that reaches a ``fallback`` rule and contains a digit becomes an Effect
  and is also recorded as unclassified, for review.
* **Warnings.** A named set that has bonuses but no name (only bare set bonus rows) is kept
  with ``name: null`` and a warning. A bug note on an entry that is not an Effect has no
  field to go in, so it is reported as a warning.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import Tag

from item_extractor.config import EnchantmentsConfig, EntryRule
from item_extractor.scraped_item import CustomisationHint, Effect, NamedSet, SetBonus


@dataclass(frozen=True)
class EffectsBlock:
    """Everything one Effects cell says, sorted into Scraped Item fields and diagnostics.

    Attributes:
        effects: The Effects, in page order.
        customisation_hints: Augment slots, Mythic/Reaper hints and item-unique systems.
        named_set: The item's named set, or None when the cell lists none.
        unclassified: Entry texts for review: no rule matched them, or they reached a
            fallback rule while containing a digit.
        rule_hits: How many entries each rule id matched.
        warnings: Human-readable notes for the run report (e.g. a nameless named set).
    """

    effects: list[Effect] = field(default_factory=list)
    customisation_hints: list[CustomisationHint] = field(default_factory=list)
    named_set: NamedSet | None = None
    unclassified: list[str] = field(default_factory=list)
    rule_hits: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def classify_effects(td: Tag, rules: EnchantmentsConfig) -> EffectsBlock:
    """Classify every entry of one Effects cell with the ordered *rules*.

    The cell is not modified. Never raises on page content: an entry no rule matches is
    listed in ``unclassified``.
    """
    effects: list[Effect] = []
    hints: list[CustomisationHint] = []
    unclassified: list[str] = []
    rule_hits: dict[str, int] = {}
    set_name: str | None = None
    set_own_bonuses: list[SetBonus] = []
    bare_bonuses: list[SetBonus] = []
    has_set = False
    warnings: list[str] = []

    cell = copy.copy(td)
    for li in (cell.find("ul") or cell).find_all("li", recursive=False):
        entry = _read_entry(li)
        if not entry.text:
            continue
        match = _first_match(rules, entry)
        if match is None:
            unclassified.append(entry.text)
            continue
        rule, groups = match
        rule_hits[rule.id] = rule_hits.get(rule.id, 0) + 1
        if entry.note and rule.kind != "effect":
            warnings.append(
                f"bug note on a {rule.kind} entry is not kept in the item: {entry.note}"
            )
        if rule.kind == "set":
            has_set = True
            set_name = groups["name"]
            set_own_bonuses = _set_bonuses(rules, entry, rule)
        elif rule.kind == "set_bonus":
            has_set = True
            bare_bonuses.append(_set_bonus(rules, entry, rule, groups))
        elif rule.kind == "hint":
            hints.append(_hint(entry, rule, groups))
        else:
            effects.append(_effect(rules, entry, rule, groups))
            if rule.fallback and re.search(r"\d", entry.text):
                unclassified.append(entry.text)

    named_set = None
    if has_set:
        named_set = NamedSet(name=set_name, bonuses=set_own_bonuses + bare_bonuses)
        if set_name is None:
            warnings.append(
                f"named set with {len(named_set.bonuses)} bonus(es) has no name"
            )
    return EffectsBlock(effects, hints, named_set, unclassified, rule_hits, warnings)


# ── Implementation ────────────────────────────────────────────────────────────


@dataclass
class _Entry:
    """The parts of one <li> the rules need."""

    text: str
    tooltip: str | None
    hrefs: list[str]
    children: list[str]
    #: <li> texts inside the tooltip (set bonus lists live here)
    tooltip_items: list[str]
    #: a trailing "(Bug: …)" note, taken off ``text``
    note: str | None = None


#: A wiki editor's bug note ending an entry: "Improved Deception +17 ( Bug: … )".
_BUG_NOTE = re.compile(r"\s+\(\s*(?P<note>Bug:[^()]*?)\s*\)$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _read_entry(li: Tag) -> _Entry:
    """Split an <li> into visible text, tooltip text, anchor hrefs and nested entries.

    Mutates *li*; the caller passes a node from its private copy of the cell.
    """
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
    text = _clean(li.get_text(" ", strip=True))
    bug = _BUG_NOTE.search(text)
    return _Entry(
        text=text[: bug.start()] if bug else text,
        tooltip=tooltip,
        hrefs=hrefs,
        children=children,
        tooltip_items=tooltip_items,
        note=bug.group("note") if bug else None,
    )


def _first_match(
    rules: EnchantmentsConfig, entry: _Entry
) -> tuple[EntryRule, dict[str, str]] | None:
    """The first rule matching *entry* and its non-empty captures, lowercased as asked."""
    for rule in rules.rules:
        when = rule.when
        if when.has_children is not None and when.has_children != bool(entry.children):
            continue
        if when.tooltip_items_match is not None and not any(
            when.tooltip_items_match.match(t) for t in entry.tooltip_items
        ):
            continue
        m = rule.pattern.match(entry.text)
        if not m:
            continue
        groups = {k: v for k, v in m.groupdict().items() if v is not None}
        for key in rule.lowercase:
            if key in groups:
                groups[key] = groups[key].lower()
        return rule, groups
    return None


def _bonus_type(
    rules: EnchantmentsConfig,
    entry: _Entry,
    rule: EntryRule,
    groups: dict[str, Any],
    tiered: bool = False,
) -> str | None:
    bt = rules.bonus_type
    if groups.get("btype"):
        return groups["btype"].lower()
    for href in entry.hrefs:
        m = bt.link_pattern.search(href)
        if m:
            return m.group(1).lower()
    source = groups.get("text") if rule.bonus_type_from == "text" else None
    if source:
        m = bt.text_pattern.search(source)
        if m:
            return m.group(1).lower()
    if entry.tooltip:
        found = [t.lower() for t in bt.tooltip_pattern.findall(entry.tooltip)]
        # A tiered Effect's tooltip naming two different Bonus Types names none.
        if found and (not tiered or len(set(found)) == 1):
            return found[0]
    return None


def _set_bonus(
    rules: EnchantmentsConfig, entry: _Entry, rule: EntryRule, groups: dict[str, Any]
) -> SetBonus:
    return SetBonus(
        pieces=int(groups["pieces"]),
        text=groups["text"],
        bonus_type=_bonus_type(rules, entry, rule, groups),
    )


def _set_bonuses(
    rules: EnchantmentsConfig, entry: _Entry, rule: EntryRule
) -> list[SetBonus]:
    assert rule.item_pattern is not None  # checked at load
    bonuses = []
    for text in entry.tooltip_items:
        m = rule.item_pattern.match(text)
        if m:
            bonuses.append(_set_bonus(rules, entry, rule, m.groupdict()))
    return bonuses


def _hint(entry: _Entry, rule: EntryRule, groups: dict[str, str]) -> CustomisationHint:
    assert rule.hint_kind is not None  # checked at load
    data: dict[str, Any] = {
        "kind": rule.hint_kind.format(**groups),
        "raw": entry.text,
        **{k: v for k, v in groups.items() if k != "hint" and v},
    }
    if entry.children:
        data["children"] = entry.children
    return CustomisationHint.model_validate(data)


def _number(raw: str | None, sign: str | None) -> float | int | None:
    if raw is None:
        return None
    value: float | int = float(raw) if "." in raw else int(raw)
    return -value if sign == "-" else value


def _tooltip_value(
    rules: EnchantmentsConfig, tooltip: str | None, value_kind: str | None
) -> tuple[float | int | None, str | None]:
    """A tiered Effect's value from its tooltip: "Greater False Life" with "+30 maximum
    health" is 30 flat. Only when the tooltip holds exactly one number and it reads as a
    bonus; two numbers (attack and damage), dice, DCs or damage ranges give no value.
    """
    tv = rules.tooltip_value
    if tv is None or tooltip is None:
        return None, value_kind
    if len(tv.number_pattern.findall(tooltip)) != 1:
        return None, value_kind
    m = tv.value_pattern.search(tooltip)
    if m is None:
        return None, value_kind
    value = _number(m.group("value"), m.groupdict().get("sign"))
    return value, "percent" if m.groupdict().get("pct") else "flat"


def _effect(
    rules: EnchantmentsConfig, entry: _Entry, rule: EntryRule, groups: dict[str, str]
) -> Effect:
    value_kind: str | None = rule.value_kind
    value: float | int | None = None
    if "tier" in groups:
        value, value_kind = rules.roman.get(groups["tier"]), "tier"
    elif "value" in groups:
        value = _number(groups["value"], groups.get("sign"))
        if groups.get("pct"):
            value_kind = "percent"
    name = _clean(rule.name.format(**groups) if rule.name else groups["name"])
    tv = rules.tooltip_value
    tiered = tv is not None and tv.name_pattern.match(name) is not None
    if value is None and tiered:
        value, value_kind = _tooltip_value(rules, entry.tooltip, value_kind)
    return Effect(
        name=name,
        value=value,
        value_kind=value_kind if value is not None else None,
        bonus_type=_bonus_type(rules, entry, rule, groups, tiered),
        tooltip=entry.tooltip,
        charges=int(groups["charges"]) if "charges" in groups else None,
        recharge_per_day=int(groups["recharge"]) if "recharge" in groups else None,
        note=entry.note,
    )
