"""Unit tests for item_normalizer.normalizer.ItemNormalizer."""

from datetime import timezone
from unittest.mock import patch

import pytest

from item_normalizer.exceptions import NormalizationError, ParseError
from item_normalizer.models import ArmorStats, DDOItem, WeaponStats
from item_normalizer.normalizer import ItemNormalizer

WIKI_URL = "https://ddowiki.com/page/Item:Test"


@pytest.fixture
def normalizer() -> ItemNormalizer:
    return ItemNormalizer()


# ---------------------------------------------------------------------------
# Full-item smoke tests
# ---------------------------------------------------------------------------


class TestNormalizeCloak:
    def test_returns_ddo_item(
        self, normalizer: ItemNormalizer, cloak_html: str
    ) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert isinstance(item, DDOItem)

    def test_name(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.name == "Mantle of the Worldshaper"

    def test_minimum_level(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.minimum_level == 20

    def test_material(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.material == "Cloth"

    def test_hardness_and_durability(
        self, normalizer: ItemNormalizer, cloak_html: str
    ) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.hardness == 14
        assert item.durability == 100

    def test_weight(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.weight == pytest.approx(0.1)

    def test_binding(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.binding == "Bound to Character on Acquire"

    def test_enchantments(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        names = [e.name for e in item.enchantments]
        assert "Metalline" in names
        assert "Resistance" in names
        assert "Superior Devotion" in names

    def test_resistance_value(
        self, normalizer: ItemNormalizer, cloak_html: str
    ) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        resistance = next(e for e in item.enchantments if e.name == "Resistance")
        assert resistance.value == 5

    def test_devotion_roman_numeral_value(
        self, normalizer: ItemNormalizer, cloak_html: str
    ) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        devotion = next(e for e in item.enchantments if e.name == "Superior Devotion")
        assert devotion.value == 6

    def test_named_set(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.named_set is not None
        assert item.named_set.name == "Thelanis Fairy Tale"

    def test_source_quests(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.source is not None
        assert "The Snitch" in item.source.quests

    def test_flavor_text(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.flavor_text is not None
        assert "planes" in item.flavor_text

    def test_no_weapon_stats(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.weapon_stats is None

    def test_no_armor_stats(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.armor_stats is None

    def test_wiki_url(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.wiki_url == WIKI_URL

    def test_scraped_at_is_utc(
        self, normalizer: ItemNormalizer, cloak_html: str
    ) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        assert item.scraped_at.tzinfo == timezone.utc

    def test_json_round_trip(self, normalizer: ItemNormalizer, cloak_html: str) -> None:
        item = normalizer.normalize(cloak_html, WIKI_URL)
        restored = DDOItem.from_json(item.to_json())
        assert restored.name == item.name
        assert len(restored.enchantments) == len(item.enchantments)


class TestNormalizeWeapon:
    def test_weapon_stats_present(
        self, normalizer: ItemNormalizer, weapon_html: str
    ) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.weapon_stats is not None
        assert isinstance(item.weapon_stats, WeaponStats)

    def test_damage_dice(self, normalizer: ItemNormalizer, weapon_html: str) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.weapon_stats.damage_dice == "1d8"

    def test_damage_bonus(self, normalizer: ItemNormalizer, weapon_html: str) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.weapon_stats.damage_bonus == 5

    def test_damage_type(self, normalizer: ItemNormalizer, weapon_html: str) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert "Slashing" in item.weapon_stats.damage_type
        assert "Magic" in item.weapon_stats.damage_type

    def test_critical_range(self, normalizer: ItemNormalizer, weapon_html: str) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.weapon_stats.critical_range == "19-20"

    def test_critical_multiplier(
        self, normalizer: ItemNormalizer, weapon_html: str
    ) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.weapon_stats.critical_multiplier == 2

    def test_enchantment_bonus(
        self, normalizer: ItemNormalizer, weapon_html: str
    ) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.weapon_stats.enchantment_bonus == 5

    def test_handedness(self, normalizer: ItemNormalizer, weapon_html: str) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.weapon_stats.handedness == "One-Handed"

    def test_weapon_type(self, normalizer: ItemNormalizer, weapon_html: str) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.weapon_stats.weapon_type == "Longsword"

    def test_no_armor_stats(self, normalizer: ItemNormalizer, weapon_html: str) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        assert item.armor_stats is None

    def test_colon_split_enchantment(
        self, normalizer: ItemNormalizer, weapon_html: str
    ) -> None:
        item = normalizer.normalize(weapon_html, WIKI_URL)
        crit = next((e for e in item.enchantments if "Critical" in e.name), None)
        assert crit is not None
        assert crit.value is None


class TestNormalizeArmor:
    def test_armor_stats_present(
        self, normalizer: ItemNormalizer, armor_html: str
    ) -> None:
        item = normalizer.normalize(armor_html, WIKI_URL)
        assert item.armor_stats is not None
        assert isinstance(item.armor_stats, ArmorStats)

    def test_armor_type(self, normalizer: ItemNormalizer, armor_html: str) -> None:
        item = normalizer.normalize(armor_html, WIKI_URL)
        assert item.armor_stats.armor_type == "Heavy"

    def test_armor_bonus(self, normalizer: ItemNormalizer, armor_html: str) -> None:
        item = normalizer.normalize(armor_html, WIKI_URL)
        assert item.armor_stats.armor_bonus == 9

    def test_max_dex_bonus(self, normalizer: ItemNormalizer, armor_html: str) -> None:
        item = normalizer.normalize(armor_html, WIKI_URL)
        assert item.armor_stats.max_dex_bonus == 3

    def test_armor_check_penalty(
        self, normalizer: ItemNormalizer, armor_html: str
    ) -> None:
        item = normalizer.normalize(armor_html, WIKI_URL)
        assert item.armor_stats.armor_check_penalty == -3

    def test_arcane_spell_failure(
        self, normalizer: ItemNormalizer, armor_html: str
    ) -> None:
        item = normalizer.normalize(armor_html, WIKI_URL)
        assert item.armor_stats.arcane_spell_failure == 25

    def test_no_weapon_stats(self, normalizer: ItemNormalizer, armor_html: str) -> None:
        item = normalizer.normalize(armor_html, WIKI_URL)
        assert item.weapon_stats is None

    def test_flavor_text(self, normalizer: ItemNormalizer, armor_html: str) -> None:
        item = normalizer.normalize(armor_html, WIKI_URL)
        assert item.flavor_text is not None
        assert "celestial" in item.flavor_text


class TestNormalizeMinimal:
    def test_minimal_item_name(
        self, normalizer: ItemNormalizer, minimal_html: str
    ) -> None:
        item = normalizer.normalize(minimal_html, WIKI_URL)
        assert item.name == "Basic Ring"

    def test_minimal_item_no_enchantments(
        self, normalizer: ItemNormalizer, minimal_html: str
    ) -> None:
        item = normalizer.normalize(minimal_html, WIKI_URL)
        assert item.enchantments == []

    def test_minimal_item_optional_fields_none(
        self, normalizer: ItemNormalizer, minimal_html: str
    ) -> None:
        item = normalizer.normalize(minimal_html, WIKI_URL)
        assert item.minimum_level is None
        assert item.weapon_stats is None
        assert item.armor_stats is None
        assert item.named_set is None
        assert item.source is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestNormalizerErrors:
    def test_parse_error_propagates(self, normalizer: ItemNormalizer) -> None:
        html = "<html><body><p>No item here</p></body></html>"
        with pytest.raises(ParseError):
            normalizer.normalize(html, WIKI_URL)

    def test_normalization_error_when_pydantic_rejects(
        self, normalizer: ItemNormalizer, cloak_html: str
    ) -> None:
        """NormalizationError wraps any exception raised during DDOItem construction."""
        with patch(
            "item_normalizer.normalizer.DDOItem", side_effect=Exception("bad data")
        ):
            with pytest.raises(NormalizationError):
                normalizer.normalize(cloak_html, WIKI_URL)


# ---------------------------------------------------------------------------
# Coercion helpers — edge cases
# ---------------------------------------------------------------------------


class TestCoerceInt:
    def test_non_numeric_string_returns_none(self, normalizer: ItemNormalizer) -> None:
        result = normalizer._coerce_int("field", "abc")
        assert result is None

    def test_none_input_returns_none(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._coerce_int("field", None) is None

    def test_numeric_string_with_suffix(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._coerce_int("minimum_level", "15 (character level)") == 15


class TestCoerceFloat:
    def test_non_numeric_string_returns_none(self, normalizer: ItemNormalizer) -> None:
        result = normalizer._coerce_float("weight", "heavy")
        assert result is None

    def test_none_input_returns_none(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._coerce_float("weight", None) is None


class TestCoercePercent:
    def test_none_input_returns_none(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._coerce_percent("arcane_spell_failure", None) is None

    def test_non_numeric_string_returns_none(self, normalizer: ItemNormalizer) -> None:
        result = normalizer._coerce_percent("arcane_spell_failure", "N/A")
        assert result is None

    def test_valid_percent_string(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._coerce_percent("arcane_spell_failure", "25%") == 25


class TestCoerceCopper:
    def test_no_currency_match_returns_none(self, normalizer: ItemNormalizer) -> None:
        result = normalizer._coerce_copper("base_value", "unknown currency")
        assert result is None

    def test_none_input_returns_none(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._coerce_copper("base_value", None) is None


class TestSuffixToInt:
    def test_empty_string_returns_none(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._suffix_to_int("") is None

    def test_unrecognized_suffix_returns_none(self, normalizer: ItemNormalizer) -> None:
        # "XQ" is not a valid Roman numeral or integer
        assert normalizer._suffix_to_int("XQ") is None

    def test_roman_numeral_returns_int(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._suffix_to_int("VI") == 6

    def test_signed_numeric_string(self, normalizer: ItemNormalizer) -> None:
        assert normalizer._suffix_to_int("+5") == 5


class TestStrategySuffixRegex:
    def test_returns_none_when_suffix_not_roman_or_digit(
        self, normalizer: ItemNormalizer
    ) -> None:
        # Regex matches but suffix "XQ" is not a known Roman numeral or digit
        result = normalizer._strategy_suffix_regex("Ability XQ")
        assert result is None

    def test_returns_enchantment_for_valid_suffix(
        self, normalizer: ItemNormalizer
    ) -> None:
        enchantment = normalizer._strategy_suffix_regex("Resistance +5")
        assert enchantment is not None
        assert enchantment.name == "Resistance"
        assert enchantment.value == 5


# ---------------------------------------------------------------------------
# Weapon stats parse failure paths
# ---------------------------------------------------------------------------


class TestParseWeaponStats:
    def test_bad_damage_string_logs_warning(self, normalizer: ItemNormalizer) -> None:
        """An unparseable damage string leaves damage_dice and damage_bonus as None."""
        fields = {
            "damage": "invalid_damage",
            "critical_roll": "19-20/x2",
            "handedness": "One-Handed",
            "weapon_type": "Longsword",
        }
        stats = normalizer._parse_weapon_stats(fields)
        assert stats is not None
        assert stats.damage_dice is None
        assert stats.damage_bonus is None

    def test_bad_crit_string_logs_warning(self, normalizer: ItemNormalizer) -> None:
        """An unparseable critical_roll leaves critical_range and multiplier as None."""
        fields = {
            "damage": "1d8+5",
            "critical_roll": "bad_crit",
            "handedness": "One-Handed",
            "weapon_type": "Longsword",
        }
        stats = normalizer._parse_weapon_stats(fields)
        assert stats is not None
        assert stats.critical_range is None
        assert stats.critical_multiplier is None

    def test_damage_type_as_string_is_split(self, normalizer: ItemNormalizer) -> None:
        """When damage_type is a plain string it is split into a list."""
        fields = {
            "damage": "1d8+5",
            "damage_type": "Slashing, Magic",
            "weapon_type": "Longsword",
        }
        stats = normalizer._parse_weapon_stats(fields)
        assert stats is not None
        assert isinstance(stats.damage_type, list)
        assert "Slashing" in stats.damage_type
        assert "Magic" in stats.damage_type

    def test_no_damage_key_leaves_damage_dice_none(
        self, normalizer: ItemNormalizer
    ) -> None:
        """When 'damage' is absent the raw_damage branch is skipped (line 371->380)."""
        fields = {"handedness": "One-handed"}
        stats = normalizer._parse_weapon_stats(fields)
        assert stats is not None
        assert stats.damage_dice is None

    def test_damage_without_bonus_leaves_damage_bonus_none(
        self, normalizer: ItemNormalizer
    ) -> None:
        """Damage string with no bonus part leaves damage_bonus as None (line 375->380)."""
        fields = {
            "damage": "1d8",
            "weapon_type": "Longsword",
        }
        stats = normalizer._parse_weapon_stats(fields)
        assert stats is not None
        assert stats.damage_dice == "1d8"
        assert stats.damage_bonus is None


# ---------------------------------------------------------------------------
# Parse enchantments — whitespace-only entry is skipped
# ---------------------------------------------------------------------------


class TestParseEnchantmentsSkipsBlank:
    def test_whitespace_only_entry_is_skipped(self, normalizer: ItemNormalizer) -> None:
        """A whitespace-only raw string triggers the 'continue' on line 329."""
        result = normalizer._parse_enchantments(["  ", "Resistance +5"])
        assert len(result) == 1
        assert result[0].name == "Resistance"
        assert result[0].value == 5

    def test_empty_string_entry_is_skipped(self, normalizer: ItemNormalizer) -> None:
        """An empty string is also skipped by the blank-entry guard."""
        result = normalizer._parse_enchantments(["", "Vorpal"])
        assert len(result) == 1
        assert result[0].name == "Vorpal"
