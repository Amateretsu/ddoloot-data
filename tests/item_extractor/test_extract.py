"""extract() on inline pages and on committed real wiki pages (tests/fixtures/pages/)."""

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from item_extractor import (
    ExtractionError,
    NotEquipmentError,
    ScrapedItem,
    aggregate,
    extract,
    load_config,
)
from item_extractor.config import DEFAULT_CONFIG_DIR
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


# The Binding cell of an Exclusive item, as the wiki writes it.
EXCLUSIVE_BINDING = (
    '<a href="/page/Bind">Bound to Account&nbsp;on Acquire</a>, '
    '<a href="/page/Exclusive">Exclusive</a>'
)


def page(filename):
    return (PAGES / filename).read_text(encoding="utf-8")


def item_page(*rows, title="Item:Row Test"):
    """A page in the real wiki shape whose infobox holds *rows* as (label, cell HTML)."""
    body = "".join(
        f'<tr><th class="bg-color-1">{label}\n</th><td>{cell}\n</td></tr>'
        for label, cell in rows
    )
    return (
        f'<html><body><h1 id="firstHeading">{title}</h1>'
        f'<div class="mw-parser-output"><table class="wikitable">{body}</table></div>'
        "</body></html>"
    )


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
    with pytest.raises(ExtractionError) as raised:
        extract("<html><body><p>hi</p></body></html>", "u", cfg)
    assert not isinstance(raised.value, NotEquipmentError)


def test_crafting_ingredient_page_is_not_equipment(cfg):
    # The real Mark of Sheshka page: an item article with no infobox, in the wiki's
    # "Raw ingredients" category.
    with pytest.raises(NotEquipmentError, match="'Raw ingredients'"):
        extract(page("Item_Mark_of_Sheshka.html"), "u", cfg)


@pytest.mark.parametrize(
    "categories",
    [
        '"wgCategories":["Binds to account","Named shields"]',
        '"wgCategories":[]',
        "",
        '"wgCategories":["Raw ingredients"',
    ],
    ids=["equipment category", "no category", "no category list", "garbled list"],
)
def test_page_without_infobox_outside_an_ingredient_category_fails(cfg, categories):
    html = page("Item_Mark_of_Sheshka.html").replace(
        '"wgCategories":["Pages needing to replace dropsfrom parameter with numbered '
        'dropsfrom","Pages needing to replace pic parameter with picdesc","Binds to '
        'account","Binds on acquire","Raw ingredients","Attack on Stormreach reward '
        'items","Blockade Buster loot"]',
        categories,
    )
    with pytest.raises(ExtractionError, match="no infobox table") as raised:
        extract(html, "u", cfg)
    assert not isinstance(raised.value, NotEquipmentError)


def test_ingredient_category_page_with_an_infobox_still_extracts(cfg):
    html = page("Item_Breaker_of_Bodies.html").replace(
        '"Named shields",', '"Named shields","Raw ingredients",'
    )
    item, _ = extract(html, "u", cfg)
    assert item.name == "Breaker of Bodies"


def test_aggregate_counts_templates_and_unmapped(cfg):
    _, r1 = extract(WEAPON, "u", cfg)
    _, r2 = extract(ACCESSORY, "u", cfg)
    summary = aggregate({"a": r1, "b": r2})
    assert summary["templates"] == {"weapon": 1, "accessory": 1}
    assert summary["unmapped_rows"] == {"mystery row": ["a"]}


# ── One row at a time: what each kind of cell becomes ───────────────────────


@pytest.mark.parametrize(
    ("label", "cell", "field", "expected"),
    [
        ("Base Value", "3 pp, 2 gp, 5 sp, 8 cp", "base_value_cp", 3_258),
        ("Base Value", "12 cp", "base_value_cp", 12),
        ("Binding", "Bound to Account on Acquire", "binding", "account"),
        ("Binding", "Unbound", "binding", "unbound"),
        ("Binding", EXCLUSIVE_BINDING, "binding", "account"),
        (
            "Binding",
            EXCLUSIVE_BINDING.replace("Account", "Character"),
            "binding",
            "character",
        ),
        ('<a href="/page/UMD">UMD</a> Difficulty', "55", "umd_dc", "55"),
        ("No UMD check for:", "Wiz, Sor", "umd_exempt_classes", "Wiz, Sor"),
        (
            'No <a href="/page/UMD">UMD</a> check for:',
            "Wiz, Sor, Clr, FvS, Brd",
            "umd_exempt_classes",
            "Wiz, Sor, Clr, FvS, Brd",
        ),
        ("Accepts Sentience?", "No", "accepts_sentience", False),
        ("Weight", "0.5 lbs", "weight", 0.5),
        ("Race\xa0Absolutely   Required", "Dwarf", "required_race", "Dwarf"),
        # A trailing colon is dropped even when whitespace follows it (the builder
        # appends a newline to every label, as the wiki does).
        ("Race Absolutely Required:", "Dwarf", "required_race", "Dwarf"),
        ("Minimum Level :\t", "12", "minimum_level", 12),
    ],
)
def test_row_is_coerced_into_its_field(cfg, label, cell, field, expected):
    item, report = extract(item_page((label, cell)), "u", cfg)
    assert getattr(item, field) == expected
    assert report["unmapped_rows"] == {}
    assert item.extraction_errors == {}


@pytest.mark.parametrize(
    ("label", "cell", "binding", "raw"),
    [
        (
            "Bind Status",
            "Bound to Character on Equip",
            "on_equip",
            "Bound to Character on Equip",
        ),
        (
            "Binding",
            EXCLUSIVE_BINDING,
            "account",
            "Bound to Account on Acquire , Exclusive",
        ),
    ],
)
def test_binding_keeps_the_raw_wiki_text(cfg, label, cell, binding, raw):
    item, _ = extract(item_page((label, cell)), "u", cfg)
    assert (item.binding, item.binding_raw) == (binding, raw)


@pytest.mark.parametrize(
    ("rows", "binding", "exclusive", "errors"),
    [
        ([("Binding", EXCLUSIVE_BINDING)], "account", True, {}),
        (
            [("Binding", EXCLUSIVE_BINDING.replace("Account", "Character"))],
            "character",
            True,
            {},
        ),
        ([("Bind Status", "Bound to Account on Acquire")], "account", False, {}),
        ([("Binding", "Unbound")], "unbound", False, {}),
        # An untimed binding stays unmapped, but the row still says whether it is
        # Exclusive.
        (
            [("Binding", "Bound to Character")],
            None,
            False,
            {"binding": "Bound to Character"},
        ),
        (
            [
                (
                    "Binding",
                    EXCLUSIVE_BINDING.replace("Account&nbsp;on Acquire", "Character"),
                )
            ],
            None,
            True,
            {"binding": "Bound to Character , Exclusive"},
        ),
        # No binding row, or one that says "None": nothing is known.
        ([("Minimum Level", "5")], None, None, {}),
        ([("Binding", "None")], None, None, {}),
    ],
)
def test_exclusive_is_read_from_the_binding_row(cfg, rows, binding, exclusive, errors):
    item, _ = extract(item_page(*rows), "u", cfg)
    assert (item.binding, item.exclusive) == (binding, exclusive)
    assert item.extraction_errors == errors


def test_weapon_damage_without_bonus_and_crit(cfg):
    item, _ = extract(
        item_page(
            ("Proficiency Class", "Simple Weapon Proficiency"),
            ("Damage", "1d8 Piercing"),
            ("Critical Roll", "19-20 / x3"),
        ),
        "u",
        cfg,
    )
    stats = item.weapon_stats
    assert (stats.damage_multiplier, stats.damage_dice, stats.damage_bonus) == (
        1.0,
        "1d8",
        0,
    )
    assert (stats.enhancement_bonus, stats.damage_types) == (0, ["Piercing"])
    assert (stats.critical_range, stats.critical_multiplier) == ("19-20", 3)


def test_armor_bonus_with_material_variants(cfg):
    item, _ = extract(
        item_page(
            ("Armor Type", "Docent"),
            ("Armor Bonus", "Adamantine Body: +26 Mithral Body: +15"),
        ),
        "u",
        cfg,
    )
    assert item.armor_stats.armor_bonus is None
    assert [(v.name, v.value) for v in item.armor_stats.armor_bonus_variants] == [
        ("Adamantine Body", 26),
        ("Mithral Body", 15),
    ]


def test_location_splits_quests_ingredients_and_detail(cfg):
    cell = (
        '<a href="/page/Item:Scale_of_Tiamat">Scale of Tiamat</a> + '
        '<a href="/page/Item:Sands_Shard">Sands Shard</a>, crafted at the altar'
    )
    item, _ = extract(item_page(("Location", cell)), "u", cfg)
    assert item.source.quests == []
    assert item.source.crafted_from == ["Scale of Tiamat", "Sands Shard"]
    assert item.source.detail == "crafted at the altar"


def test_ignored_rows_are_reported_not_mapped(cfg):
    _, report = extract(
        item_page(("Rarity", "Rare"), ("School", "Evocation")), "u", cfg
    )
    assert report["ignored_rows"] == ["rarity", "school"]
    assert report["unmapped_rows"] == {}


def test_spread_row_without_target_records_errors_under_its_first_label(tmp_path):
    config_dir = tmp_path / "extractor"
    shutil.copytree(DEFAULT_CONFIG_DIR, config_dir)
    path = config_dir / "fields.yaml"
    data = yaml.safe_load(path.read_text())
    next(r for r in data["fields"] if r["coerce"] == "binding").pop("target")
    path.write_text(yaml.safe_dump(data))

    item, _ = extract(
        item_page(("Bind Status", "Bound to nobody")), "u", load_config(config_dir)
    )
    assert item.binding is None
    assert item.extraction_errors == {"binding": "Bound to nobody"}


# ── The Effects cell: what each kind of entry becomes ─────────────────────────


def effects_page(*entries):
    """A page whose Enchantments cell lists *entries* (each the inner HTML of one <li>)."""
    items = "".join(f"<li>{e}</li>" for e in entries)
    return item_page(("Enchantments", f"<ul>{items}</ul>"))


def extract_effects(cfg, *entries):
    return extract(effects_page(*entries), "u", cfg)


@pytest.mark.parametrize(
    ("text", "name", "value", "value_kind"),
    [
        ("Corrosion +59", "Corrosion", 59, "flat"),
        ("Ice Lore +22%", "Ice Lore", 22, "percent"),
        ("Fortification  +94%", "Fortification", 94, "percent"),
        ("Doublestrike 6%", "Doublestrike", 6, "percent"),
        ("+7 Enhancement Bonus", "Enhancement Bonus", 7, "flat"),
        ("Bloodletter VII", "Bloodletter", 7, "tier"),
        ("Maximum Charge Tier : V", "Maximum Charge Tier", 5, "tier"),
        ("Holy Burst 4", "Holy Burst", 4, "number"),
        ("Adds +0.5 weapon dice multiplier", "weapon dice multiplier", 0.5, "flat"),
        ("Armor Class -2", "Armor Class", -2, "flat"),
    ],
)
def test_effect_entry_value(cfg, text, name, value, value_kind):
    item, report = extract_effects(cfg, text)
    [effect] = item.effects
    assert (effect.name, effect.value, effect.value_kind) == (name, value, value_kind)
    assert report["unclassified_effects"] == []


def test_plain_effect_has_no_value_and_is_not_flagged(cfg):
    item, report = extract_effects(cfg, "Antipodal")
    assert (item.effects[0].name, item.effects[0].value) == ("Antipodal", None)
    assert report["unclassified_effects"] == []
    assert report["rule_hits"] == {"plain_effect": 1}


def test_fallback_entry_with_digits_is_kept_and_flagged(cfg):
    item, report = extract_effects(cfg, "Weird 3x thing")
    assert [e.name for e in item.effects] == ["Weird 3x thing"]
    assert report["unclassified_effects"] == ["Weird 3x thing"]


@pytest.mark.parametrize(
    ("entry", "name", "charges", "recharge"),
    [
        ('<a href="/page/Rage_(spell)">Rage</a> — 3 Charges', "Rage", 3, None),
        (
            (
                '<a href="/page/Disrupt_Undead">Disrupt Undead</a> — 50 Charges '
                "(Recharged/Day:50)"
            ),
            "Disrupt Undead",
            50,
            50,
        ),
        (
            (
                '<a href="/page/Negative_Energy_Absorption">Negative Energy Absorption'
                "</a> - 10 Charges (Recharged/Day:&nbsp; 5)"
                '<span class="tooltip">Absorbs negative energy.</span>'
            ),
            "Negative Energy Absorption",
            10,
            5,
        ),
        (
            "Delayed Blast Fireball (Instant) — 15 Charges",
            "Delayed Blast Fireball (Instant)",
            15,
            None,
        ),
    ],
)
def test_clicky_is_its_spell_with_charges_and_recharge(
    cfg, entry, name, charges, recharge
):
    item, report = extract_effects(cfg, entry)
    [effect] = item.effects
    assert (effect.name, effect.charges, effect.recharge_per_day) == (
        name,
        charges,
        recharge,
    )
    assert (effect.value, effect.value_kind, effect.bonus_type) == (None, None, None)
    assert report["unclassified_effects"] == []
    assert report["rule_hits"] == {"clicky": 1}


@pytest.mark.parametrize(
    "text", ["Anti-Magic - 3 Charged Bolts", "Charges - 3", "Spell Charges 3"]
)
def test_entry_that_only_mentions_charges_is_not_a_clicky(cfg, text):
    item, report = extract_effects(cfg, text)
    assert [(e.charges, e.recharge_per_day) for e in item.effects] == [(None, None)]
    assert "clicky" not in report["rule_hits"]


@pytest.mark.parametrize(
    ("entry", "name", "value", "note"),
    [
        (
            (
                '<span class="popup"><a href="/page/Deception">Improved Deception +17'
                '</a><span class="tooltip">Improved Deception: +17 to Bluff.</span>'
                "</span> (<b>Bug: </b> Provides +5 to bluff, not +17)"
            ),
            "Improved Deception",
            17,
            "Bug: Provides +5 to bluff, not +17",
        ),
        ("Antipodal (Bug:&nbsp;does nothing)", "Antipodal", None, "Bug: does nothing"),
    ],
)
def test_bug_note_is_kept_apart_from_the_effect(cfg, entry, name, value, note):
    item, report = extract_effects(cfg, entry)
    [effect] = item.effects
    assert (effect.name, effect.value, effect.note) == (name, value, note)
    assert report["unclassified_effects"] == []


@pytest.mark.parametrize(
    "text",
    [
        "Strength +5 (Bug: fixed in U14) extra",
        "Strength +5 (bug: lower case)",
        "Strength +5 (Note: not a bug)",
    ],
)
def test_entry_without_a_trailing_bug_note_keeps_its_text(cfg, text):
    item, _ = extract_effects(cfg, text)
    assert [(e.name, e.note) for e in item.effects] == [(text, None)]


def test_bug_note_on_a_hint_is_reported_not_dropped(cfg):
    item, report = extract_effects(cfg, "Blue Augment Slot (Bug: cannot be slotted)")
    assert [h.kind for h in item.customisation_hints] == ["augment_slot"]
    assert report["warnings"] == [
        "bug note on a hint entry is not kept in the item: Bug: cannot be slotted"
    ]


def test_entry_no_rule_matches_is_unclassified_and_does_not_raise(tmp_path):
    config_dir = tmp_path / "extractor"
    shutil.copytree(DEFAULT_CONFIG_DIR, config_dir)
    path = config_dir / "enchantments.yaml"
    data = yaml.safe_load(path.read_text())
    data["rules"] = [r for r in data["rules"] if not r.get("fallback")]
    path.write_text(yaml.safe_dump(data))

    item, report = extract(
        effects_page("Corrosion +59", "Antipodal"), "u", load_config(config_dir)
    )
    assert [e.name for e in item.effects] == ["Corrosion"]
    assert report["unclassified_effects"] == ["Antipodal"]
    assert report["rule_hits"] == {"signed_suffix": 1}


def test_bonus_type_from_tooltip_link(cfg):
    item, _ = extract_effects(
        cfg,
        '<span class="popup"><a href="/page/x">Corrosion +59</a>'
        '<span class="popup tooltip">Corrosion +59: +59 '
        '<a href="/page/Equipment_bonus">Equipment bonus</a></span></span>',
    )
    [effect] = item.effects
    assert (effect.name, effect.bonus_type) == ("Corrosion", "equipment")
    assert effect.tooltip.startswith("Corrosion +59")


def test_bonus_type_from_tooltip_text_when_no_link(cfg):
    item, _ = extract_effects(
        cfg,
        'Quality Combat Mastery +3<span class="tooltip">Quality Combat Mastery +3 : '
        "+3 Quality bonus to the DC.</span>",
    )
    assert item.effects[0].bonus_type == "quality"


def test_tooltip_is_read_though_other_cells_have_theirs_stripped(cfg):
    effects_cell = (
        '<ul><li>Strength +5<span class="tooltip">+5 '
        '<a href="/page/Insight_bonus">Insight bonus</a></span></li></ul>'
    )
    html = item_page(
        ("Minimum Level", '5<span class="tooltip">Level 5 tooltip</span>'),
        ("Enchantments", effects_cell),
    )
    item, _ = extract(html, "u", cfg)
    assert item.minimum_level == 5
    assert item.effects[0].bonus_type == "insight"
    assert item.effects[0].tooltip == "+5 Insight bonus"


def test_bonus_to_entry_takes_type_from_its_text(cfg):
    item, _ = extract_effects(
        cfg, "+6% Artifact bonus to Fire, Cold and Acid Spell Critical Chance"
    )
    [effect] = item.effects
    assert (effect.bonus_type, effect.value) == ("artifact", 6)
    assert effect.name.startswith("Fire, Cold")


@pytest.mark.parametrize(
    ("text", "hint_kind"),
    [
        ("Blue Augment Slot", "augment_slot"),
        ("Mythic Weapon Boost +2 or +4", "mythic"),
        ("Reaper Enhancement", "reaper"),
        ("Upgradeable - Primary Augment ( Yellow )", "augment_upgrade"),
    ],
)
def test_customisation_hints_are_not_effects(cfg, text, hint_kind):
    item, _ = extract_effects(cfg, text)
    assert item.effects == []
    assert [h.kind for h in item.customisation_hints] == [hint_kind]


def test_nested_list_is_a_unique_system_hint(cfg):
    item, _ = extract_effects(
        cfg,
        "Nearly Finished<ul><li>Quality Intelligence +1</li>"
        "<li>Quality Wisdom +1</li></ul>",
    )
    [hint] = item.customisation_hints
    assert hint.kind == "unique_system"
    assert hint.children == ["Quality Intelligence +1", "Quality Wisdom +1"]
    assert item.effects == []


SET_ROW = (
    "Huntmaster's Favor"
    '<span class="tooltip"><ul>'
    "<li>2 Pieces Equipped: +3 Artifact bonus to Sneak Attack Dice</li>"
    "<li>3 Pieces Equipped: +15% Artifact bonus to Doublestrike</li></ul></span>"
)
BARE_BONUS_ROW = "5 Pieces Equipped: +10 Insight bonus to Melee Power"


def test_named_set_takes_its_bonuses_from_the_tooltip(cfg):
    item, report = extract_effects(cfg, SET_ROW)
    named_set = item.named_set
    assert named_set.name == "Huntmaster's Favor"
    assert [(b.pieces, b.bonus_type) for b in named_set.bonuses] == [
        (2, "artifact"),
        (3, "artifact"),
    ]
    assert item.effects == []
    assert report["warnings"] == []


def test_set_row_and_bare_set_bonus_rows_merge_the_same_in_any_order(cfg):
    set_first, report_a = extract_effects(cfg, SET_ROW, "Keen", BARE_BONUS_ROW)
    set_last, report_b = extract_effects(cfg, BARE_BONUS_ROW, "Keen", SET_ROW)
    assert set_first.named_set == set_last.named_set
    assert set_first.named_set.name == "Huntmaster's Favor"
    assert [b.pieces for b in set_first.named_set.bonuses] == [2, 3, 5]
    assert set_first.named_set.bonuses[2].bonus_type == "insight"
    assert [e.name for e in set_last.effects] == ["Keen"]
    assert report_a["warnings"] == report_b["warnings"] == []


def test_set_bonuses_without_a_set_name_are_kept_with_a_warning(cfg):
    item, report = extract_effects(
        cfg, BARE_BONUS_ROW, "3 Pieces Equipped: +2 Artifact bonus to Strength"
    )
    assert item.named_set.name is None
    assert [b.pieces for b in item.named_set.bonuses] == [5, 3]
    assert report["warnings"] == ["named set with 2 bonus(es) has no name"]


def test_page_without_an_effects_row_has_empty_effects_and_no_warnings(cfg):
    item, report = extract(item_page(("Minimum Level", "5")), "u", cfg)
    assert (item.effects, item.customisation_hints, item.named_set) == ([], [], None)
    assert report["rule_hits"] == {}
    assert report["warnings"] == []


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
    # "5.20[1d8+2] + 15 Pierce, Magic": multiplier, base dice and bonus, enhancement bonus.
    stats = item.weapon_stats
    assert (stats.damage_multiplier, stats.damage_dice, stats.damage_bonus) == (
        5.2,
        "1d8",
        2,
    )
    assert stats.enhancement_bonus == 15, "the same +15 the Effects list shows"
    assert stats.damage_types == ["Piercing", "Magic"]
    assert item.extraction_errors == {}
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
    # "[1d8] + 5 Bludgeon, Magic": no multiplier means 1.0, and the +5 is the enhancement
    # bonus, not a damage bonus.
    stats = item.weapon_stats
    assert (stats.damage_multiplier, stats.damage_dice, stats.damage_bonus) == (
        1.0,
        "1d8",
        0,
    )
    assert stats.enhancement_bonus == 5
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

# Every extractor gap the held Item pages show, by page title and kind, reviewed and
# committed. Backlog runs update it deliberately after reviewing a batch's new gaps:
#   UPDATE_GAPS_BASELINE=1 .venv/bin/pytest -q tests/item_extractor -k whole_page_store
# The update rewrites the entries of held pages and keeps those of pages not held here.
GAPS_BASELINE = Path(__file__).with_name("extractor_gaps.json")


def page_gaps(cached, cfg):
    """The gaps extract() shows on one held page, as {kind: sorted values}."""
    try:
        item, report = extract(cached.html, cached.url, cfg)
    except NotEquipmentError as exc:
        return {"skipped": [str(exc)]}
    except ExtractionError as exc:
        return {"extraction_failed": [str(exc)]}
    gaps = {
        "extraction_errors": sorted(item.extraction_errors),
        "extraction_failed": [] if item.name else ["no item name"],
        "unclassified_effects": sorted(set(report["unclassified_effects"])),
        "unmapped_rows": sorted(report["unmapped_rows"]),
        "warnings": sorted(set(report["warnings"])),
    }
    return {kind: values for kind, values in gaps.items() if values}


def test_whole_page_store_extracts_with_only_the_baselined_gaps(cfg):
    pages = list(PageStore(load_scraper_config()).iter_cached())
    if not pages:
        pytest.skip("the Page Store (config/scraper.yaml cache_dir) holds no pages")
    held = {page.title: page for page in pages if page.title.startswith("Item:")}
    found = {
        title: gaps for title, page in held.items() if (gaps := page_gaps(page, cfg))
    }
    baseline = json.loads(GAPS_BASELINE.read_text(encoding="utf-8"))

    if os.environ.get("UPDATE_GAPS_BASELINE") == "1":
        unheld = {title: gaps for title, gaps in baseline.items() if title not in held}
        baseline = unheld | found
        GAPS_BASELINE.write_text(
            json.dumps(baseline, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # Entries for pages not held here are not checked. A held page's entry must match
    # exactly: a new gap is a regression or unreviewed, and a fixed gap is removed from
    # the baseline by the update command so the baseline never overstates the gaps.
    expected = {title: gaps for title, gaps in baseline.items() if title in held}
    assert found == expected, (
        f"held Item pages' gaps differ from {GAPS_BASELINE.name}; review them, then "
        "rerun with UPDATE_GAPS_BASELINE=1"
    )
