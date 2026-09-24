"""The Scraped Item: the record of one Named Item as read from its wiki page.

This module is the interface of the extractor's output. Every field is always written:
``None`` (or an empty list) means the wiki row was absent or said "None"; ``None`` with an
``extraction_errors`` entry means a coercer could not parse that row's raw text.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Union

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WikiRef(_Strict):
    """Where the page came from (CC BY-SA attribution)."""

    page_id: int | None = None
    revision_id: int | None = None
    title: str | None = None
    url: str


class WeaponStats(_Strict):
    proficiency: str | None = None
    weapon_type: str | None = None
    handedness: str | None = None
    damage_multiplier: float | None = None
    damage_dice: str | None = None
    damage_bonus: int | None = None
    enhancement_bonus: int | None = None
    damage_types: list[str] | None = None
    critical_range: str | None = None
    critical_multiplier: int | None = None
    attack_mod: str | None = None
    damage_mod: str | None = None


class ArmorBonusVariant(_Strict):
    name: str
    value: int


class ArmorStats(_Strict):
    armor_type: str | None = None
    armor_bonus: int | None = None
    armor_bonus_variants: list[ArmorBonusVariant] | None = None
    max_dex_bonus: int | None = None
    armor_check_penalty: int | None = None
    arcane_spell_failure: int | None = None


class ShieldStats(_Strict):
    shield_type: str | None = None
    shield_bonus: int | None = None
    damage_reduction: int | None = None


class Effect(_Strict):
    """One Effect with its raw name, value and Bonus Type."""

    name: str
    value: int | float | None = None
    value_kind: str | None = None
    bonus_type: str | None = None
    tooltip: str | None = None


class CustomisationHint(_Strict):
    """An entry that hints at a Customisation (augment slot, Mythic, Reaper, ...)."""

    kind: str
    raw: str
    name: str | None = None
    colour: str | None = None
    text: str | None = None
    children: list[str] = []


class SetBonus(_Strict):
    pieces: int
    text: str
    bonus_type: str | None = None


class NamedSet(_Strict):
    name: str | None = None
    bonuses: list[SetBonus] = []


class Source(_Strict):
    quests: list[str] = []
    detail: str | None = None
    crafted_from: list[str] = []
    raw: str | None = None


class ScrapedItem(_Strict):
    """One Named Item as read from its wiki page, before registry or rules apply."""

    # identity
    name: str | None = None
    wiki: WikiRef
    template: str
    category: str
    item_type: str | None = None
    equip_slots: list[str] = []
    # requirements
    minimum_level: int | None = None
    required_race: str | None = None
    excluded_race: str | None = None
    required_class: str | None = None
    required_feat: str | None = None
    required_trait: str | None = None
    # properties
    binding: str | None = None
    binding_raw: str | None = None
    material: str | None = None
    hardness: int | None = None
    durability: int | None = None
    base_value_cp: int | None = None
    weight: float | None = None
    upgradeable: str | None = None
    accepts_sentience: bool | None = None
    umd_dc: str | None = None
    # stats
    weapon_stats: WeaponStats | None = None
    armor_stats: ArmorStats | None = None
    shield_stats: ShieldStats | None = None
    # effects
    effects: list[Effect] = []
    customisation_hints: list[CustomisationHint] = []
    named_set: NamedSet | None = None
    # text
    flavor_text: str | None = None
    notes: str | None = None
    tips: str | None = None
    source: Source | None = None
    # diagnostics: dotted field path -> raw text a coercer could not parse
    extraction_errors: dict[str, str] = {}


def has_field(path: str) -> bool:
    """Whether a dotted path such as ``weapon_stats.damage_dice`` names a ScrapedItem field."""
    model: Any = ScrapedItem
    for key in path.split("."):
        if model is None or key not in model.model_fields:
            return False
        model = _submodel(model.model_fields[key].annotation)
    return True


def _submodel(annotation: Any) -> type[BaseModel] | None:
    """The BaseModel inside ``Model | None``, else None."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if typing.get_origin(annotation) in (Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None
