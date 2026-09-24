"""Shared fixtures for item_db tests.

Provides an in-memory ItemRepository and sample DDOItem fixtures covering
accessories, weapons, and armor.
"""

from datetime import datetime, timezone

import pytest

from item_db import ItemRepository
from item_normalizer.models import (
    ArmorStats,
    DDOItem,
    Enchantment,
    ItemSource,
    NamedSet,
    SetBonus,
    WeaponStats,
)

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def repo() -> ItemRepository:
    """In-memory ItemRepository, opened and ready for use."""
    with ItemRepository(":memory:") as r:
        yield r


@pytest.fixture
def cloak_item() -> DDOItem:
    """A fully populated cloak DDOItem."""
    return DDOItem(
        name="Mantle of the Worldshaper",
        item_type="Cloak",
        slot="Back",
        minimum_level=20,
        binding="Bound to Character on Acquire",
        material="Cloth",
        hardness=14,
        durability=100,
        base_value=1265000,
        weight=0.1,
        flavor_text="This cloak was woven from the fabric of the planes themselves.",
        wiki_url="https://ddowiki.com/page/Item:Mantle_of_the_Worldshaper",
        scraped_at=_TS,
        enchantments=[
            Enchantment(name="Superior Devotion", value=6),
            Enchantment(name="Resistance", value=5),
            Enchantment(name="Metalline"),
            Enchantment(name="Good Luck", value=2),
        ],
        named_set=NamedSet(
            name="Thelanis Fairy Tale",
            bonuses=[
                SetBonus(
                    pieces_required=2, description="+1 artifact bonus to all saves"
                )
            ],
        ),
        source=ItemSource(
            quests=["The Snitch", "The Spinner of Shadows"],
        ),
    )


@pytest.fixture
def weapon_item() -> DDOItem:
    """A fully populated longsword DDOItem."""
    return DDOItem(
        name="Sword of Shadow",
        item_type="Weapon",
        slot="Main Hand",
        minimum_level=15,
        binding="Bound to Character on Equip",
        hardness=18,
        durability=150,
        base_value=840000,
        weight=4.0,
        wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow",
        scraped_at=_TS,
        enchantments=[
            Enchantment(name="Vorpal"),
            Enchantment(name="Improved Critical"),
            Enchantment(name="Shadowstrike", value=3),
        ],
        weapon_stats=WeaponStats(
            damage_dice="1d8",
            damage_bonus=5,
            damage_type=["Slashing", "Magic"],
            critical_range="19-20",
            critical_multiplier=2,
            enchantment_bonus=5,
            handedness="One-Handed",
            proficiency="Martial Weapon Proficiency",
            weapon_type="Longsword",
        ),
        source=ItemSource(quests=["The Pit"]),
    )


@pytest.fixture
def armor_item() -> DDOItem:
    """A fully populated heavy armor DDOItem."""
    return DDOItem(
        name="Plate of the Fallen",
        item_type="Armor",
        slot="Body",
        minimum_level=18,
        binding="Bound to Character on Acquire",
        material="Mithral",
        hardness=20,
        durability=200,
        base_value=2400000,
        weight=50.0,
        flavor_text="Forged from the armor of a fallen celestial.",
        wiki_url="https://ddowiki.com/page/Item:Plate_of_the_Fallen",
        scraped_at=_TS,
        enchantments=[
            Enchantment(name="Greater Fortification"),
        ],
        armor_stats=ArmorStats(
            armor_type="Heavy",
            armor_bonus=9,
            max_dex_bonus=3,
            armor_check_penalty=-3,
            arcane_spell_failure=25,
        ),
    )


@pytest.fixture
def minimal_item() -> DDOItem:
    """A bare-minimum DDOItem with only required fields."""
    return DDOItem(
        name="Basic Ring",
        wiki_url="https://ddowiki.com/page/Item:Basic_Ring",
        scraped_at=_TS,
    )
