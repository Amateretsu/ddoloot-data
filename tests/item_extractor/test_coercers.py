import pytest

from item_extractor.coercers import COERCERS


def run(name, text, cfg, cell=None):
    return COERCERS[name](text, cell, cfg)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10,803 pp 5 gp", 10_803_500),
        ("3 pp, 2 gp, 5 sp, 8 cp", 3_258),
        ("12 cp", 12),
        ("free", None),
    ],
)
def test_copper(cfg, text, expected):
    assert run("copper", text, cfg) == expected


def test_damage_with_brackets_and_types(cfg):
    out = run("damage", "[1d10] + 8 Slash, Magic", cfg)
    assert out == {
        "weapon_stats.damage_dice": "1d10",
        "weapon_stats.damage_bonus": 8,
        "weapon_stats.damage_types": ["Slashing", "Magic"],
    }


def test_damage_without_bonus(cfg):
    out = run("damage", "1d8 Piercing", cfg)
    assert out["weapon_stats.damage_bonus"] is None
    assert out["weapon_stats.damage_types"] == ["Piercing"]


def test_damage_unparseable_is_kept_raw(cfg):
    assert run("damage", "varies", cfg) == {"weapon_stats.damage_raw": "varies"}


def test_crit(cfg):
    assert run("crit", "17-20 / x2", cfg) == {
        "weapon_stats.critical_range": "17-20",
        "weapon_stats.critical_multiplier": 2,
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bound to Account on Acquire", "account"),
        ("Bound to Character on Equip", "on_equip"),
        ("Something new", None),
    ],
)
def test_binding_maps_and_keeps_raw(cfg, text, expected):
    out = run("binding", text, cfg)
    assert out == {"binding": expected, "binding_raw": text}


def test_armor_bonus_plain_and_variants(cfg):
    assert run("armor_bonus", "+9", cfg) == {"armor_stats.armor_bonus": 9}
    out = run("armor_bonus", "Adamantine Body: +26 Mithral Body: +15", cfg)
    assert out["armor_stats.armor_bonus"] is None
    assert out["armor_stats.armor_bonus_variants"] == [
        {"name": "Adamantine Body", "value": 26},
        {"name": "Mithral Body", "value": 15},
    ]


def test_yes_no(cfg):
    assert run("yes_no", "No", cfg) is False
    assert run("yes_no", "Maybe", cfg) is None
