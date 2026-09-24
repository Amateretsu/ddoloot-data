"""Unit tests for item_normalizer.models."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from item_normalizer.models import (
    ArmorStats,
    DDOItem,
    Enchantment,
    NamedSet,
    SetBonus,
    WeaponStats,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(**overrides) -> DDOItem:
    defaults = {
        "name": "Test Item",
        "wiki_url": "https://ddowiki.com/page/Item:Test_Item",
        "scraped_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return DDOItem(**defaults)


# ---------------------------------------------------------------------------
# Enchantment
# ---------------------------------------------------------------------------


class TestEnchantment:
    def test_name_only(self) -> None:
        enc = Enchantment(name="Metalline")
        assert enc.name == "Metalline"
        assert enc.value is None

    def test_name_and_value(self) -> None:
        enc = Enchantment(name="Resistance", value=5)
        assert enc.value == 5

    def test_frozen(self) -> None:
        enc = Enchantment(name="Metalline")
        with pytest.raises(ValidationError):
            enc.name = "Changed"  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Enchantment(name="Metalline", unknown_field="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# WeaponStats
# ---------------------------------------------------------------------------


class TestWeaponStats:
    def test_all_optional(self) -> None:
        ws = WeaponStats()
        assert ws.damage_dice is None
        assert ws.damage_type == []

    def test_full_construction(self) -> None:
        ws = WeaponStats(
            damage_dice="1d8",
            damage_bonus=5,
            damage_type=["Slashing", "Magic"],
            critical_range="19-20",
            critical_multiplier=2,
            enchantment_bonus=5,
            handedness="One-Handed",
            proficiency="Martial Weapon Proficiency",
            weapon_type="Longsword",
        )
        assert ws.damage_dice == "1d8"
        assert ws.critical_multiplier == 2
        assert "Magic" in ws.damage_type

    def test_frozen(self) -> None:
        ws = WeaponStats(damage_dice="1d6")
        with pytest.raises(ValidationError):
            ws.damage_dice = "2d6"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ArmorStats
# ---------------------------------------------------------------------------


class TestArmorStats:
    def test_all_optional(self) -> None:
        a = ArmorStats()
        assert a.armor_type is None

    def test_full_construction(self) -> None:
        a = ArmorStats(
            armor_type="Heavy",
            armor_bonus=9,
            max_dex_bonus=3,
            armor_check_penalty=-3,
            arcane_spell_failure=25,
        )
        assert a.armor_check_penalty == -3
        assert a.arcane_spell_failure == 25


# ---------------------------------------------------------------------------
# NamedSet / SetBonus
# ---------------------------------------------------------------------------


class TestNamedSet:
    def test_no_bonuses(self) -> None:
        ns = NamedSet(name="Thelanis Fairy Tale")
        assert ns.bonuses == []

    def test_with_bonuses(self) -> None:
        ns = NamedSet(
            name="Thelanis Fairy Tale",
            bonuses=[SetBonus(pieces_required=2, description="+1 artifact bonus")],
        )
        assert len(ns.bonuses) == 1
        assert ns.bonuses[0].pieces_required == 2


# ---------------------------------------------------------------------------
# DDOItem
# ---------------------------------------------------------------------------


class TestDDOItem:
    def test_minimal_construction(self) -> None:
        item = _make_item()
        assert item.name == "Test Item"
        assert item.minimum_level is None
        assert item.enchantments == []

    def test_all_optional_fields_are_none(self) -> None:
        item = _make_item()
        for field in (
            "item_type",
            "slot",
            "required_race",
            "required_class",
            "binding",
            "material",
            "weapon_stats",
            "armor_stats",
            "named_set",
            "source",
            "flavor_text",
        ):
            assert getattr(item, field) is None

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _make_item(unknown_field="x")

    def test_frozen(self) -> None:
        item = _make_item()
        with pytest.raises(ValidationError):
            item.name = "Changed"  # type: ignore[misc]

    def test_to_json_round_trip(self) -> None:
        item = _make_item(
            minimum_level=15,
            enchantments=[Enchantment(name="Resistance", value=5)],
        )
        json_str = item.to_json()
        parsed = json.loads(json_str)
        assert parsed["name"] == "Test Item"
        assert parsed["minimum_level"] == 15
        assert parsed["enchantments"][0]["name"] == "Resistance"

    def test_from_json_round_trip(self) -> None:
        item = _make_item(minimum_level=10, flavor_text="Ancient and powerful.")
        restored = DDOItem.from_json(item.to_json())
        assert restored.name == item.name
        assert restored.minimum_level == item.minimum_level
        assert restored.flavor_text == item.flavor_text
        assert restored.scraped_at == item.scraped_at

    def test_to_json_indent_default(self) -> None:
        item = _make_item()
        json_str = item.to_json()
        assert "\n" in json_str  # indented

    def test_to_json_custom_indent(self) -> None:
        item = _make_item()
        json_str = item.to_json(indent=4)
        assert "    " in json_str  # 4-space indent present
