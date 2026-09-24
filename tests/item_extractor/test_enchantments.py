import pytest
from bs4 import BeautifulSoup

from item_extractor.enchantments import classify, read_entry


def li(html):
    return BeautifulSoup(f"<ul>{html}</ul>", "html.parser").find("li")


def run(cfg, html):
    return classify(cfg, read_entry(li(html)))


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
def test_effect_values(cfg, text, name, value, value_kind):
    result = run(cfg, f"<li>{text}</li>")
    assert result.kind == "effect"
    assert result.data["name"] == name
    assert result.data["value"] == value
    assert result.data["value_kind"] == value_kind


def test_plain_effect_has_no_value_and_is_not_flagged(cfg):
    result = run(cfg, "<li>Antipodal</li>")
    assert result.data["value"] is None
    assert result.unclassified is False


def test_fallback_with_digits_is_flagged(cfg):
    assert run(cfg, "<li>Weird 3x thing</li>").unclassified is True


def test_bonus_type_from_tooltip_link(cfg):
    html = (
        '<li><span class="popup"><a href="/page/x">Corrosion +59</a>'
        '<span class="popup tooltip">Corrosion +59: +59 <a href="/page/Equipment_bonus">Equipment bonus</a></span></span></li>'
    )
    result = run(cfg, html)
    assert result.data["bonus_type"] == "equipment"
    assert result.data["tooltip"].startswith("Corrosion +59")


def test_bonus_type_from_tooltip_text_when_no_link(cfg):
    html = '<li>Quality Combat Mastery +3<span class="tooltip">Quality Combat Mastery +3 : +3 Quality bonus to the DC.</span></li>'
    assert run(cfg, html).data["bonus_type"] == "quality"


def test_bonus_to_rule_takes_type_from_text(cfg):
    result = run(
        cfg, "<li>+6% Artifact bonus to Fire, Cold and Acid Spell Critical Chance</li>"
    )
    assert result.data["bonus_type"] == "artifact"
    assert result.data["value"] == 6
    assert result.data["name"].startswith("Fire, Cold")


@pytest.mark.parametrize(
    ("text", "hint_kind"),
    [
        ("Blue Augment Slot", "augment_slot"),
        ("Mythic Weapon Boost +2 or +4", "mythic"),
        ("Reaper Enhancement", "reaper"),
        ("Upgradeable - Primary Augment ( Yellow )", "augment_upgrade"),
    ],
)
def test_hints_are_not_effects(cfg, text, hint_kind):
    result = run(cfg, f"<li>{text}</li>")
    assert result.kind == "hint"
    assert result.data["kind"] == hint_kind


def test_nested_list_is_a_unique_system(cfg):
    result = run(
        cfg,
        "<li>Nearly Finished<ul><li>Quality Intelligence +1</li><li>Quality Wisdom +1</li></ul></li>",
    )
    assert result.kind == "hint"
    assert result.data["kind"] == "unique_system"
    assert result.data["children"] == ["Quality Intelligence +1", "Quality Wisdom +1"]


def test_set_membership_comes_from_tooltip_items(cfg):
    html = (
        "<li>Huntmaster's Favor"
        '<span class="tooltip"><ul><li>2 Pieces Equipped: +3 Artifact bonus to Sneak Attack Dice</li>'
        "<li>3 Pieces Equipped: +15% Artifact bonus to Doublestrike</li></ul></span></li>"
    )
    result = run(cfg, html)
    assert result.kind == "set"
    assert result.data["name"] == "Huntmaster's Favor"
    assert [b["pieces"] for b in result.data["bonuses"]] == [2, 3]
    assert result.data["bonuses"][0]["bonus_type"] == "artifact"
