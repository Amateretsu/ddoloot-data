"""extract() on inline pages and on committed real wiki pages (tests/fixtures/pages/)."""

from pathlib import Path

import pytest

from item_extractor import ExtractionError, ScrapedItem, aggregate, extract
from page_store import PageStore, load_scraper_config

PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "pages"

WEAPON = """
<html><head><script>RLCONF={"wgArticleId":40486,"wgRevisionId":649113};</script></head><body>
<h1 id="firstHeading">Item:Test Blade</h1>
<div class="mw-parser-output"><table class="wikitable">
<tr><th>Proficiency Class</th><td>Exotic Weapon Proficiency</td></tr>
<tr><th>Damage and Type</th><td>[1d10] + 8 Slash, Magic</td></tr>
<tr><th>Critical threat range</th><td>17-20 / x2</td></tr>
<tr><th>Weapon Type</th><td>Bastard Sword / Slashing weapons</td></tr>
<tr><th>Minimum Level</th><td>27</td></tr>
<tr><th>Binding</th><td>Bound to Character&nbsp;on Equip</td></tr>
<tr><th>Race Absolutely Required</th><td>None</td></tr>
<tr><th>Base Value</th><td><span class="sortkey">0010803500</span> 10,803 pp 5 gp</td></tr>
<tr><th>Location</th><td><b><a href="/page/The_Thrill">The Thrill</a></b>, End chest</td></tr>
<tr><th>Enchantments</th><td><ul><li>+8 Enhancement Bonus</li><li>Keen</li></ul></td></tr>
<tr><th>Mystery Row</th><td>Something</td></tr>
</table></div></body></html>
"""

ACCESSORY = """
<html><body><h1 id="firstHeading">Item:Test Ring</h1>
<div class="mw-parser-output"><table>
<tr><th>Minimum Level</th><td>5</td></tr>
<tr><th>Item Type</th><td>Jewelry / Ring</td></tr>
<tr><th>Slot</th><td>Finger</td></tr>
<tr><th>Material</th><td>Gold</td></tr>
<tr><th>Enhancements</th><td><ul><li>Strength +5</li></ul></td></tr>
</table></div></body></html>
"""

UNPARSEABLE = """
<html><body><h1 id="firstHeading">Item:Odd Blade</h1>
<div class="mw-parser-output"><table>
<tr><th>Proficiency Class</th><td>Simple Weapon Proficiency</td></tr>
<tr><th>Damage and Type</th><td>varies</td></tr>
<tr><th>Minimum Level</th><td>Level unknown</td></tr>
<tr><th>Base Value</th><td>free</td></tr>
<tr><th>Binding</th><td>Bound to a mysterious force</td></tr>
<tr><th>Accepts Sentience?</th><td>Maybe</td></tr>
<tr><th>Hardness</th><td>None</td></tr>
</table></div></body></html>
"""


def page(filename):
    return (PAGES / filename).read_text(encoding="utf-8")


def test_weapon_page(cfg):
    item, report = extract(WEAPON, "https://ddowiki.com/page/Item:Test_Blade", cfg)
    assert item.template == "weapon"
    assert item.category == "weapon"
    assert item.name == "Test Blade"
    assert item.wiki.page_id == 40486
    assert item.wiki.revision_id == 649113
    assert item.binding == "on_equip"
    assert item.base_value_cp == 10_803_500
    assert item.item_type == "Bastard Sword"
    assert item.equip_slots == ["main_hand"]
    assert item.weapon_stats.damage_dice == "1d10"
    assert item.weapon_stats.damage_types == ["Slashing", "Magic"]
    assert item.weapon_stats.critical_multiplier == 2
    assert item.required_race is None, "the string 'None' is null, not a value"
    assert item.extraction_errors == {}
    assert item.source.quests == ["The Thrill"]
    assert item.source.detail == "End chest"
    assert [e.name for e in item.effects] == ["Enhancement Bonus", "Keen"]
    assert report["unmapped_rows"] == {"mystery row": "Something"}


def test_accessory_page_derives_category_and_slot(cfg):
    item, report = extract(ACCESSORY, "u", cfg)
    assert item.template == "accessory"
    assert item.category == "jewelry"
    assert item.item_type == "Ring"
    assert item.equip_slots == ["finger"]
    assert "slot" not in item.model_dump()
    assert item.effects[0].value == 5, "the Enhancements label is an effects list"
    assert report["unmapped_rows"] == {}


def test_unparseable_rows_are_null_with_their_raw_text_recorded(cfg):
    item, _ = extract(UNPARSEABLE, "u", cfg)
    assert item.weapon_stats.damage_dice is None
    assert item.minimum_level is None
    assert item.base_value_cp is None
    assert item.binding is None
    assert item.accepts_sentience is None
    assert item.extraction_errors == {
        "weapon_stats.damage_dice": "varies",
        "minimum_level": "Level unknown",
        "base_value_cp": "free",
        "binding": "Bound to a mysterious force",
        "accepts_sentience": "Maybe",
    }
    assert "hardness" not in item.extraction_errors, "'None' is absent, not an error"


def test_every_field_is_written_even_when_unknown(cfg):
    item, _ = extract(ACCESSORY, "u", cfg)
    dumped = item.model_dump(mode="json")
    assert set(dumped) == set(ScrapedItem.model_fields)
    assert dumped["weapon_stats"] is None
    assert dumped["named_set"] is None
    assert dumped["hardness"] is None


def test_page_without_infobox_raises(cfg):
    with pytest.raises(ExtractionError):
        extract("<html><body><p>hi</p></body></html>", "u", cfg)


def test_aggregate_counts_templates_and_unmapped(cfg):
    _, r1 = extract(WEAPON, "u", cfg)
    _, r2 = extract(ACCESSORY, "u", cfg)
    summary = aggregate({"a": r1, "b": r2})
    assert summary["templates"] == {"weapon": 1, "accessory": 1}
    assert summary["unmapped_rows"] == {"mystery row": ["a"]}


# ── Committed real pages ──────────────────────────────────────────────────────


def test_real_weapon_page(cfg):
    url = "https://ddowiki.com/page/Item:Legendary_Gnollish_War_Bow"
    item, report = extract(page("Item_Legendary_Gnollish_War_Bow.html"), url, cfg)
    assert item.name == "Legendary Gnollish War Bow"
    assert item.wiki.url == url
    assert item.wiki.revision_id == 632346
    assert (item.template, item.category, item.item_type) == (
        "weapon",
        "weapon",
        "Long Bow",
    )
    assert item.minimum_level == 31
    assert item.binding == "on_equip"
    assert item.base_value_cp == 12_407_500
    assert item.weapon_stats.critical_range == "20"
    assert item.weapon_stats.critical_multiplier == 3
    assert item.effects[0].name == "Enhancement Bonus"
    assert item.effects[0].value == 15
    assert [h.colour for h in item.customisation_hints] == ["Red", "Purple"]
    assert item.source.quests == ["Attack on Stormreach"]
    # The wiki writes weapon dice as "5.20[1d8+2] + 15 ...", which the damage coercer
    # does not read yet: the field is null and the raw text is kept for review.
    assert item.weapon_stats.damage_dice is None
    assert item.extraction_errors == {
        "weapon_stats.damage_dice": "5.20[1d8+2] + 15 Pierce, Magic"
    }
    assert report["unmapped_rows"] == {}
    assert report["unclassified_effects"] == []


def test_real_armor_page(cfg):
    item, _ = extract(page("Item_Full_Plate_of_the_Ringleader.html"), "u", cfg)
    assert (item.template, item.category, item.item_type) == (
        "armor",
        "armor",
        "Heavy Armor",
    )
    assert item.equip_slots == ["body"]
    assert item.excluded_race == "Warforged"
    assert item.required_feat == "Heavy Armor Proficiency"
    assert item.armor_stats.armor_bonus == 11
    assert item.armor_stats.max_dex_bonus == 1
    assert item.armor_stats.armor_check_penalty == -5
    assert item.armor_stats.arcane_spell_failure == 35
    assert item.weapon_stats is None
    assert {e.name: e.bonus_type for e in item.effects}["Charisma"] == "enhancement"
    assert item.extraction_errors == {}


def test_real_shield_page(cfg):
    item, _ = extract(page("Item_Breaker_of_Bodies.html"), "u", cfg)
    assert (item.template, item.category, item.item_type) == (
        "shield",
        "shield",
        "Large Shield",
    )
    assert item.equip_slots == ["off_hand"]
    assert item.shield_stats.shield_bonus == 9
    assert item.shield_stats.damage_reduction == 9
    assert item.weapon_stats.damage_dice == "1d8"
    assert item.weapon_stats.damage_bonus == 5
    assert item.extraction_errors == {}


def test_real_accessory_page(cfg):
    item, _ = extract(page("Item_Bound_Elemental_Ring_of_Frost.html"), "u", cfg)
    assert (item.template, item.category, item.item_type) == (
        "accessory",
        "jewelry",
        "Ring",
    )
    assert item.equip_slots == ["finger"]
    assert item.binding == "character"
    assert item.weight == 0.1
    ice_lore = item.effects[0]
    assert (ice_lore.name, ice_lore.value, ice_lore.value_kind) == (
        "Ice Lore",
        22,
        "percent",
    )
    assert ice_lore.bonus_type == "equipment"
    assert [h.kind for h in item.customisation_hints] == [
        "augment_slot",
        "augment_slot",
        "mythic",
    ]
    assert item.extraction_errors == {}


def test_real_untyped_accessory_page(cfg):
    item, _ = extract(page("Item_Epic_Whirling_Words.html"), "u", cfg)
    assert (item.template, item.category, item.item_type) == (
        "accessory_untyped",
        "other",
        None,
    )
    assert item.equip_slots == []
    assert item.required_trait == "Artificer Rune Arm Use"
    assert ("Maximum Charge Tier", 5, "tier") in [
        (e.name, e.value, e.value_kind) for e in item.effects
    ]
    assert item.extraction_errors == {}


def test_real_page_with_none_values_and_no_effects(cfg):
    item, report = extract(
        page("Item_Dark_Ressurectionist_s_Frock_Vest.html"), "u", cfg
    )
    assert (item.template, item.item_type) == ("armor", "Cosmetic Armor")
    assert item.effects == []
    assert item.customisation_hints == []
    # Rows that say "None" or are blank are null and are not extraction errors.
    assert item.minimum_level is None
    assert item.required_feat is None
    assert item.base_value_cp is None
    assert item.armor_stats.armor_bonus is None
    assert item.extraction_errors == {}
    assert report["unmapped_rows"] == {}


# ── Whole Page Store (skipped when empty) ─────────────────────────────────────


def test_whole_page_store_extracts_with_no_unmapped_rows_or_unclassified_effects(cfg):
    pages = list(PageStore(load_scraper_config()).iter_cached())
    if not pages:
        pytest.skip("the Page Store (config/scraper.yaml cache_dir) holds no pages")
    reports = {}
    for page in pages:
        if not page.title.startswith("Item:"):
            continue
        item, reports[page.title] = extract(page.html, page.url, cfg)
        assert item.name, page.title
    summary = aggregate(reports)
    assert summary["unmapped_rows"] == {}
    assert summary["unclassified_effects"] == {}
