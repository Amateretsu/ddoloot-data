"""Pydantic models for normalized DDO item data.

All models are frozen (immutable after construction) and reject unknown fields.
Optional fields are None when the wiki page does not contain them.

Example:
    >>> from item_normalizer.models import DDOItem, Enchantment
    >>> item = DDOItem(
    ...     name="Mantle of the Worldshaper",
    ...     wiki_url="https://ddowiki.com/page/Item:Mantle_of_the_Worldshaper",
    ...     scraped_at=datetime.now(timezone.utc),
    ... )
    >>> print(item.to_json())
    {
      "name": "Mantle of the Worldshaper",
      ...
    }
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Enchantment(BaseModel):
    """A single magical property on an item.

    Attributes:
        name: The enchantment name (e.g. 'Resistance', 'Superior Devotion')
        value: The enchantment tier/magnitude as an integer if present
               (e.g. Resistance +5 → 5, Armor Class -2 → -2, Superior Devotion VI → 6)

    Example:
        >>> Enchantment(name="Resistance", value=5)
        Enchantment(name='Resistance', value=5)
        >>> Enchantment(name="Metalline")
        Enchantment(name='Metalline', value=None)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: Optional[int] = None


class WeaponStats(BaseModel):
    """Weapon-specific stats. Present only when the item is a weapon.

    Attributes:
        damage_dice: Dice notation for base damage (e.g. '1d8', '2d6')
        damage_bonus: Flat bonus added to damage roll (e.g. 5 from '1d8+5')
        damage_type: List of damage types (e.g. ['Slashing', 'Magic'])
        critical_range: Threat range string (e.g. '19-20', '20')
        critical_multiplier: Critical hit multiplier (e.g. 2, 3)
        enchantment_bonus: The weapon's enhancement bonus (e.g. 5 for a +5 weapon)
        handedness: How the weapon is wielded (e.g. 'One-Handed', 'Two-Handed')
        proficiency: Required proficiency (e.g. 'Martial Weapon Proficiency')
        weapon_type: Specific weapon category (e.g. 'Longsword', 'Greataxe')

    Example:
        >>> WeaponStats(
        ...     damage_dice="1d8",
        ...     damage_bonus=5,
        ...     damage_type=["Slashing", "Magic"],
        ...     critical_range="17-20",
        ...     critical_multiplier=2,
        ...     enchantment_bonus=5,
        ...     handedness="One-Handed",
        ...     proficiency="Martial Weapon Proficiency",
        ...     weapon_type="Longsword",
        ... )
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    damage_dice: Optional[str] = None
    damage_bonus: Optional[int] = None
    damage_type: list[str] = Field(default_factory=list)
    critical_range: Optional[str] = None
    critical_multiplier: Optional[int] = None
    enchantment_bonus: Optional[int] = None
    handedness: Optional[str] = None
    proficiency: Optional[str] = None
    weapon_type: Optional[str] = None


class ArmorStats(BaseModel):
    """Armor-specific stats. Present only when the item is armor or a shield.

    Attributes:
        armor_type: Armor category ('Light', 'Medium', 'Heavy', 'Shield', 'Docent')
        armor_bonus: Armor class bonus granted
        max_dex_bonus: Maximum dexterity bonus allowed while wearing
        armor_check_penalty: Penalty applied to physical skill checks
        arcane_spell_failure: Percentage chance of arcane spell failure

    Example:
        >>> ArmorStats(
        ...     armor_type="Heavy",
        ...     armor_bonus=9,
        ...     max_dex_bonus=3,
        ...     armor_check_penalty=-3,
        ...     arcane_spell_failure=25,
        ... )
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    armor_type: Optional[str] = None
    armor_bonus: Optional[int] = None
    max_dex_bonus: Optional[int] = None
    armor_check_penalty: Optional[int] = None
    arcane_spell_failure: Optional[int] = None


class SetBonus(BaseModel):
    """A bonus granted by equipping a required number of pieces from a named set.

    Attributes:
        pieces_required: Number of set pieces needed to activate this bonus
        description: Text description of the bonus effect

    Example:
        >>> SetBonus(pieces_required=2, description="+1 artifact bonus to all saves")
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pieces_required: int
    description: str


class NamedSet(BaseModel):
    """Named item set membership and any associated set bonuses.

    Attributes:
        name: Name of the set (e.g. 'Thelanis Fairy Tale')
        bonuses: List of set bonuses keyed by number of pieces required

    Example:
        >>> NamedSet(
        ...     name="Thelanis Fairy Tale",
        ...     bonuses=[SetBonus(pieces_required=2, description="+1 artifact bonus")]
        ... )
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    bonuses: list[SetBonus] = Field(default_factory=list)


class ItemSource(BaseModel):
    """Where the item can be obtained in game.

    Attributes:
        quests: List of quest names where the item drops
        chest: Named chest the item can be found in
        dropped_by: List of monster names that drop the item
        crafted_by: Crafting recipe or NPC if the item is crafted

    Example:
        >>> ItemSource(quests=["The Shroud"], chest="Altar of Fecundity")
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    quests: list[str] = Field(default_factory=list)
    chest: Optional[str] = None
    dropped_by: list[str] = Field(default_factory=list)
    crafted_by: Optional[str] = None


class DDOItem(BaseModel):
    """Fully normalized representation of a DDO Wiki item page.

    All optional fields are None when the wiki page does not contain them.
    The item is immutable after construction.

    Attributes:
        name: Item name as it appears on the wiki
        item_type: Item category (e.g. 'Cloak', 'Ring', 'Longsword')
        slot: Equipment slot (e.g. 'Back', 'Finger', 'Body')
        minimum_level: Minimum character level required to equip
        required_race: Race restriction if any
        required_class: Class restriction if any
        binding: Binding type (e.g. 'Bound to Character on Acquire')
        material: Physical material (e.g. 'Cloth', 'Cold Iron', 'Mithral')
        hardness: Item hardness for damage resistance calculations
        durability: Maximum durability points
        base_value: Sale value in copper pieces (1 pp = 1000 cp, 1 gp = 100 cp, 1 sp = 10 cp)
        weight: Weight in pounds
        enchantments: List of magical properties on the item
        weapon_stats: Weapon-specific stats; None if item is not a weapon
        armor_stats: Armor-specific stats; None if item is not armor
        named_set: Named set membership; None if item is not part of a set
        source: Where the item can be obtained; None if unknown
        flavor_text: Italic lore description from the wiki page
        wiki_url: Full URL of the wiki page this item was parsed from
        scraped_at: UTC timestamp of when the page was fetched and normalized

    Example:
        >>> from datetime import datetime, timezone
        >>> item = DDOItem(
        ...     name="Mantle of the Worldshaper",
        ...     wiki_url="https://ddowiki.com/page/Item:Mantle_of_the_Worldshaper",
        ...     scraped_at=datetime.now(timezone.utc),
        ... )
        >>> json_str = item.to_json()
        >>> restored = DDOItem.from_json(json_str)
        >>> restored.name == item.name
        True
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Core identity
    name: str
    item_type: Optional[str] = None
    slot: Optional[str] = None

    # Requirements
    minimum_level: Optional[int] = None
    required_race: Optional[str] = None
    required_class: Optional[str] = None

    # Binding
    binding: Optional[str] = None

    # Physical properties
    material: Optional[str] = None
    hardness: Optional[int] = None
    durability: Optional[int] = None
    base_value: Optional[int] = None
    weight: Optional[float] = None

    # Enchantments
    enchantments: list[Enchantment] = Field(default_factory=list)

    # Type-specific stats (mutually exclusive in practice)
    weapon_stats: Optional[WeaponStats] = None
    armor_stats: Optional[ArmorStats] = None

    # Set membership
    named_set: Optional[NamedSet] = None

    # Source
    source: Optional[ItemSource] = None

    # Flavor
    flavor_text: Optional[str] = None

    # Metadata
    wiki_url: str
    scraped_at: datetime

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize this item to a formatted JSON string.

        The scraped_at datetime is serialized as an ISO 8601 string.

        Args:
            indent: Number of spaces for JSON indentation

        Returns:
            JSON string representation of the item

        Example:
            >>> json_str = item.to_json()
            >>> '"name"' in json_str
            True
        """
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "DDOItem":
        """Deserialize a DDOItem from a JSON string.

        Args:
            json_str: A JSON string previously produced by to_json()

        Returns:
            Reconstructed DDOItem instance

        Raises:
            pydantic.ValidationError: If the JSON does not match the schema

        Example:
            >>> item = DDOItem.from_json(json_str)
            >>> isinstance(item, DDOItem)
            True
        """
        return cls.model_validate_json(json_str)
