"""Named coercers referenced from fields.yaml.

``COERCERS`` is the seam between ``fields.yaml`` and code: a row names its coercer, and
``load_config()`` rejects a name that is not registered here. Coercers are implementation
behind ``extract()``; they are tested through it, not one by one.

A coercer takes ``(text, cell, cfg)`` and returns either one value (for a plain target)
or, for ``spread`` fields, a ``{dotted.path: value}`` dict. ``cell`` is the row's ``<td>``
Tag and ``cfg`` the typed :class:`~item_extractor.config.Config`. A coercer that cannot
read the text raises :class:`Unparseable`; the extractor then leaves the field null and
records the raw text in ``extraction_errors``. A spread coercer may still pass the values
it could read in ``Unparseable.partial``, and those are set.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from bs4 import Tag

Coercer = Callable[[str, Any, Any], Any]


class Unparseable(ValueError):
    """The cell text does not have the shape this coercer reads."""

    def __init__(self, text: str, partial: dict[str, Any] | None = None) -> None:
        super().__init__(text)
        self.partial = partial or {}


# "5.20[1d8+2] + 15 Pierce, Magic": weapon-dice multiplier, base dice and bonus inside the
# brackets, then the enhancement bonus and the damage types. The multiplier and the
# brackets are optional ("[1d8] + 5 Bludgeon", "1d8 Piercing").
_DAMAGE_RE = re.compile(
    r"""^(?:
        (?:(?P<mult>\d+(?:\.\d+)?)\s*)?
        \[\s*(?P<dice>\d+d\d+)\s*(?:(?P<bsign>[+-])\s*(?P<bonus>\d+))?\s*\]
        | (?P<bare>\d+d\d+)
    )
    \s*(?:(?P<esign>[+-])\s*(?P<enh>\d+))?
    \s*(?P<rest>.*)$""",
    re.I | re.X,
)
_CRIT_RE = re.compile(r"(\d+(?:-\d+)?)\s*/\s*[xX×]?(\d+)")  # noqa: RUF001
_INT_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
_VARIANT_RE = re.compile(r"([A-Za-z][A-Za-z ]*?):\s*\+?(\d+)")
# The wiki's ", Exclusive" after the binding ("Bound to Account on Acquire , Exclusive").
_EXCLUSIVE_RE = re.compile(r"\s*,\s*exclusive$", re.I)


def _int(text: str, cell: Any, cfg: Any) -> int | None:
    m = _INT_RE.search(text)
    if not m:
        raise Unparseable(text)
    return int(m.group())


def _float(text: str, cell: Any, cfg: Any) -> float | None:
    m = _FLOAT_RE.search(text)
    if not m:
        raise Unparseable(text)
    return float(m.group())


def _text(text: str, cell: Any, cfg: Any) -> str:
    return text


def _first_segment(text: str, cell: Any, cfg: Any) -> str:
    return text.split(" / ", maxsplit=1)[0].strip()


def _yes_no(text: str, cell: Any, cfg: Any) -> bool:
    lowered = text.lower()
    if lowered in ("yes", "true"):
        return True
    if lowered in ("no", "false"):
        return False
    raise Unparseable(text)


def _copper(text: str, cell: Any, cfg: Any) -> int:
    denominations = cfg.mappings.denominations
    total, found = 0, False
    for m in re.finditer(r"([\d,]+)\s*(pp|gp|sp|cp)\b", text, re.I):
        total += int(m.group(1).replace(",", "")) * denominations[m.group(2).lower()]
        found = True
    if not found:
        raise Unparseable(text)
    return total


def _binding(text: str, cell: Any, cfg: Any) -> dict[str, Any]:
    # Exclusive is read even when the binding itself is not mapped.
    base, suffixes = _EXCLUSIVE_RE.subn("", text)
    exclusive = suffixes > 0
    binding = cfg.mappings.binding.get(base.lower())
    if binding is None:
        raise Unparseable(text, partial={"exclusive": exclusive})
    return {"binding": binding, "binding_raw": text, "exclusive": exclusive}


def _damage(text: str, cell: Any, cfg: Any) -> dict[str, Any]:
    m = _DAMAGE_RE.match(text)
    if not m:
        raise Unparseable(text)
    types = [
        cfg.mappings.damage_types.get(t.strip().lower(), t.strip())
        for t in re.split(r"[,/]", m["rest"])
        if t.strip()
    ]
    # An omitted part has its neutral value: "[1d8]" is 1.0 x [1d8+0] + 0.
    return {
        "weapon_stats.damage_multiplier": float(m["mult"]) if m["mult"] else 1.0,
        "weapon_stats.damage_dice": m["dice"] or m["bare"],
        "weapon_stats.damage_bonus": _signed(m["bsign"], m["bonus"]),
        "weapon_stats.enhancement_bonus": _signed(m["esign"], m["enh"]),
        "weapon_stats.damage_types": types,
    }


def _signed(sign: str | None, digits: str | None) -> int:
    return int(digits) * (-1 if sign == "-" else 1) if digits else 0


def _crit(text: str, cell: Any, cfg: Any) -> dict[str, Any]:
    m = _CRIT_RE.search(text)
    if not m:
        raise Unparseable(text)
    return {
        "weapon_stats.critical_range": m.group(1),
        "weapon_stats.critical_multiplier": int(m.group(2)),
    }


def _armor_bonus(text: str, cell: Any, cfg: Any) -> dict[str, Any]:
    variants = [
        {"name": n.strip(), "value": int(v)} for n, v in _VARIANT_RE.findall(text)
    ]
    if variants:
        return {
            "armor_stats.armor_bonus": None,
            "armor_stats.armor_bonus_variants": variants,
        }
    return {"armor_stats.armor_bonus": _int(text, cell, cfg)}


def _location(text: str, cell: Tag, cfg: Any) -> dict[str, Any]:
    """Split a Location cell using its markup: bold links are quests, Item: links are
    crafting ingredients, and whatever text remains is the chest/reward description."""
    quests: list[str] = []
    crafted_from: list[str] = []
    for a in cell.find_all("a"):
        href = a.get("href", "")
        name = a.get_text(strip=True)
        if not name:
            continue
        if href.startswith("/page/Item:"):
            crafted_from.append(name)
        elif a.find_parent("b") is not None:
            quests.append(name)
    if not quests and not crafted_from:
        head = text.split(",", maxsplit=1)[0].strip()
        quests = [head] if head else []
    remainder = text
    for name in quests + crafted_from:
        remainder = remainder.replace(name, "")
    remainder = re.sub(r"[+,]\s*", " ", remainder).strip(" ,+")
    remainder = re.sub(r"\s+", " ", remainder).strip()
    return {
        "source.quests": quests,
        "source.crafted_from": crafted_from,
        "source.detail": remainder or None,
        "source.raw": text,
    }


COERCERS: dict[str, Coercer] = {
    "int": _int,
    "float": _float,
    "text": _text,
    "first_segment": _first_segment,
    "yes_no": _yes_no,
    "copper": _copper,
    "binding": _binding,
    "damage": _damage,
    "crit": _crit,
    "armor_bonus": _armor_bonus,
    "location": _location,
}
