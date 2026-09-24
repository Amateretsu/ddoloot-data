import json
from pathlib import Path

import pytest

from item_extractor import ExtractionError, aggregate, extract

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


def test_weapon_page(cfg):
    item, report = extract(WEAPON, "https://ddowiki.com/page/Item:Test_Blade", cfg)
    assert item["template"] == "weapon"
    assert item["category"] == "weapon"
    assert item["name"] == "Test Blade"
    assert item["wiki"]["page_id"] == 40486
    assert item["wiki"]["revision_id"] == 649113
    assert item["binding"] == "on_equip"
    assert item["base_value_cp"] == 10_803_500
    assert item["item_type"] == "Bastard Sword"
    assert item["equip_slots"] == ["main_hand"]
    assert item["weapon_stats"]["damage_dice"] == "1d10"
    assert item["weapon_stats"]["critical_multiplier"] == 2
    assert "required_race" not in item, "the string 'None' is null, not a value"
    assert item["source"]["quests"] == ["The Thrill"]
    assert item["source"]["detail"] == "End chest"
    assert [e["name"] for e in item["effects"]] == ["Enhancement Bonus", "Keen"]
    assert report["unmapped_rows"] == {"mystery row": "Something"}


def test_accessory_page_derives_category_and_slot(cfg):
    item, report = extract(ACCESSORY, "u", cfg)
    assert item["template"] == "accessory"
    assert item["category"] == "jewelry"
    assert item["item_type"] == "Ring"
    assert item["equip_slots"] == ["finger"]
    assert "slot" not in item
    assert item["effects"][0]["value"] == 5, "the Enhancements label is an effects list"
    assert report["unmapped_rows"] == {}


def test_page_without_infobox_raises(cfg):
    with pytest.raises(ExtractionError):
        extract("<html><body><p>hi</p></body></html>", "u", cfg)


def test_aggregate_counts_templates_and_unmapped(cfg):
    _, r1 = extract(WEAPON, "u", cfg)
    _, r2 = extract(ACCESSORY, "u", cfg)
    summary = aggregate({"a": r1, "b": r2})
    assert summary["templates"] == {"weapon": 1, "accessory": 1}
    assert summary["unmapped_rows"] == {"mystery row": ["a"]}


CACHE = Path(__file__).resolve().parents[2] / "cache"


@pytest.mark.skipif(not (CACHE / "index.json").exists(), reason="no local sample cache")
def test_local_sample_cache_has_no_unmapped_rows_or_failures(cfg):
    index = json.loads((CACHE / "index.json").read_text())
    reports = {}
    for fname, meta in index.items():
        html = (CACHE / "html" / fname).read_text(encoding="utf-8")
        _, reports[meta["name"]] = extract(html, meta["url"], cfg)
    summary = aggregate(reports)
    assert summary["unmapped_rows"] == {}
    assert summary["unclassified_effects"] == {}
