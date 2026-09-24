"""item_normalizer — DDO Wiki item normalization engine.

Public API:
    ItemNormalizer  : parse + normalize an item page into a DDOItem
    DDOItem         : the fully normalized item model
    Enchantment     : a single magical property
    WeaponStats     : weapon-specific combat stats
    ArmorStats      : armor-specific defensive stats
    NamedSet        : named set membership
    ItemSource      : where the item can be obtained
    ParseError      : raised when HTML structure is unrecognizable
    NormalizationError : raised when normalized data fails validation

Example:
    >>> from item_normalizer import ItemNormalizer
    >>> normalizer = ItemNormalizer()
    >>> item = normalizer.normalize(html, wiki_url="https://ddowiki.com/page/Item:Foo")
    >>> print(item.to_json())
"""

from item_normalizer.exceptions import (
    ItemNormalizerError,
    NormalizationError,
    ParseError,
)
from item_normalizer.models import (
    ArmorStats,
    DDOItem,
    Enchantment,
    ItemSource,
    NamedSet,
    SetBonus,
    WeaponStats,
)
from item_normalizer.normalizer import ItemNormalizer

__all__ = [
    "ArmorStats",
    "DDOItem",
    "Enchantment",
    "ItemNormalizer",
    "ItemNormalizerError",
    "ItemSource",
    "NamedSet",
    "NormalizationError",
    "ParseError",
    "SetBonus",
    "WeaponStats",
]
