"""Normalizer for DDO Wiki item data.

Converts a raw ParsedFields dict (from WikiPageParser) into a validated DDOItem
Pydantic model. All type coercion happens here; the parser only extracts strings.

Three-tier error handling:
  - ParseError    : fatal, re-raised (bad HTML structure, no item name)
  - Field-level   : logged at WARNING, field set to None (coercion failed)
  - NormalizationError : reserved for programming errors (Pydantic rejects assembled data)

Example:
    >>> from item_normalizer.normalizer import ItemNormalizer
    >>> normalizer = ItemNormalizer()
    >>> item = normalizer.normalize(html, wiki_url="https://ddowiki.com/page/Item:Foo")
    >>> item.name
    'Foo'
    >>> item.minimum_level
    15
"""

import re
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from item_normalizer.exceptions import NormalizationError
from item_normalizer.models import (
    ArmorStats,
    DDOItem,
    Enchantment,
    ItemSource,
    NamedSet,
    WeaponStats,
)
from item_normalizer.parser import ParsedFields, WikiPageParser

# Regex to split a damage string like "1d8+5" into dice and bonus parts.
_DAMAGE_RE = re.compile(r"^(\d+d\d+)([+-]\d+)?", re.IGNORECASE)

# Regex to match a critical roll string like "19-20/x3" or "20/x2".
_CRIT_RE = re.compile(r"(\d+(?:-\d+)?)\s*/\s*[xX×]?(\d+)")  # noqa: RUF001

# Regex to detect a trailing numeric value in an enchantment string: "+5", "-2", "VI".
_ENCHANT_SUFFIX_RE = re.compile(
    r"^(.*?)\s+([+-]\d+|(?:X{0,3})(?:IX|IV|V?I{0,3}))$",
    re.IGNORECASE,
)

# Roman numeral → integer mapping for enchantment tier suffixes.
_ROMAN: dict[str, int] = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
}


class ItemNormalizer:
    """Converts raw HTML from a DDO Wiki item page into a validated DDOItem.

    Internally creates a WikiPageParser to extract raw field strings, then
    performs all type coercion and sub-model assembly.

    Example:
        >>> normalizer = ItemNormalizer()
        >>> item = normalizer.normalize(html, wiki_url="https://ddowiki.com/page/Item:Foo")
        >>> isinstance(item, DDOItem)
        True
    """

    def __init__(self) -> None:
        self._parser = WikiPageParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(self, html: str, wiki_url: str) -> DDOItem:
        """Parse HTML and return a fully normalized DDOItem.

        Args:
            html: Raw HTML string from the wiki page
            wiki_url: Full URL of the page, injected verbatim into the result

        Returns:
            Validated DDOItem instance

        Raises:
            ParseError: If the HTML has no recognizable item structure
            NormalizationError: If the assembled data is rejected by Pydantic

        Example:
            >>> item = normalizer.normalize(html, wiki_url="https://ddowiki.com/page/Item:Foo")
            >>> item.wiki_url
            'https://ddowiki.com/page/Item:Foo'
        """
        # ParseError propagates directly — fatal, nothing to normalize.
        fields: ParsedFields = self._parser.parse(html, wiki_url)
        logger.debug(f"Raw fields extracted: {list(fields.keys())}")

        try:
            item = DDOItem(
                name=fields["name"],
                wiki_url=fields["wiki_url"],
                scraped_at=datetime.now(timezone.utc),
                item_type=fields.get("item_type"),
                slot=fields.get("slot"),
                minimum_level=self._coerce_int(
                    "minimum_level", fields.get("minimum_level")
                ),
                required_race=fields.get("required_race"),
                required_class=fields.get("required_class"),
                binding=fields.get("binding"),
                material=fields.get("material"),
                hardness=self._coerce_int("hardness", fields.get("hardness")),
                durability=self._coerce_int("durability", fields.get("durability")),
                base_value=self._coerce_copper("base_value", fields.get("base_value")),
                weight=self._coerce_float("weight", fields.get("weight")),
                enchantments=self._parse_enchantments(fields.get("enchantments", [])),
                weapon_stats=self._parse_weapon_stats(fields),
                armor_stats=self._parse_armor_stats(fields),
                named_set=self._parse_named_set(fields.get("named_set_raw")),
                source=self._parse_source(fields.get("location")),
                flavor_text=fields.get("flavor_text"),
            )
        except Exception as exc:
            raise NormalizationError(
                f"Pydantic rejected assembled data for '{fields.get('name')}': {exc}",
                field="DDOItem",
            ) from exc

        logger.info(f"Normalized item: {item.name!r} (ml={item.minimum_level})")
        return item

    # ------------------------------------------------------------------
    # Coercion helpers
    # ------------------------------------------------------------------

    def _coerce_int(self, field: str, raw: Optional[str]) -> Optional[int]:
        """Convert a raw string to int, or None if missing / unparseable.

        Strips non-digit characters beyond the leading number so that values
        like '15 (character level)' are handled gracefully.

        Args:
            field: Field name for logging context
            raw: Raw string value

        Returns:
            Integer value or None

        Example:
            >>> normalizer._coerce_int("minimum_level", "15")
            15
            >>> normalizer._coerce_int("minimum_level", "15 (character level)")
            15
            >>> normalizer._coerce_int("minimum_level", None)
            # returns None
        """
        if raw is None:
            return None
        match = re.search(r"-?\d+", raw)
        if match:
            return int(match.group())
        logger.warning(f"Could not coerce {field!r} to int: {raw!r}")
        return None

    def _coerce_float(self, field: str, raw: Optional[str]) -> Optional[float]:
        """Convert a raw string to float, or None if missing / unparseable.

        Args:
            field: Field name for logging context
            raw: Raw string value

        Returns:
            Float value or None

        Example:
            >>> normalizer._coerce_float("weight", "3.5")
            3.5
        """
        if raw is None:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", raw)
        if match:
            return float(match.group())
        logger.warning(f"Could not coerce {field!r} to float: {raw!r}")
        return None

    def _coerce_percent(self, field: str, raw: Optional[str]) -> Optional[int]:
        """Convert a percentage string like '25%' to an integer, or None.

        Args:
            field: Field name for logging context
            raw: Raw string value

        Returns:
            Integer percentage (e.g. 25) or None

        Example:
            >>> normalizer._coerce_percent("arcane_spell_failure", "25%")
            25
        """
        if raw is None:
            return None
        match = re.search(r"-?\d+", raw)
        if match:
            return int(match.group())
        logger.warning(f"Could not coerce {field!r} to percent int: {raw!r}")
        return None

    def _coerce_copper(self, field: str, raw: Optional[str]) -> Optional[int]:
        """Convert a currency string to total copper pieces.

        DDO currency denominations:
            1 pp = 1000 cp,  1 gp = 100 cp,  1 sp = 10 cp,  1 cp = 1 cp

        Handles single-denomination values ("3,620 pp") and compound values
        ("3 pp, 2 gp, 5 sp, 8 cp"). Commas in numbers are ignored.

        Args:
            field: Field name for logging context
            raw: Raw string value from the infobox

        Returns:
            Total value in copper pieces, or None if unparseable

        Example:
            >>> normalizer._coerce_copper("base_value", "3,620 pp")
            3620000
            >>> normalizer._coerce_copper("base_value", "2 gp, 5 sp")
            250
        """
        if raw is None:
            return None

        _DENOM = {"pp": 1000, "gp": 100, "sp": 10, "cp": 1}
        total = 0
        found = False
        for m in re.finditer(r"([\d,]+)\s*(pp|gp|sp|cp)", raw, re.IGNORECASE):
            amount = int(m.group(1).replace(",", ""))
            total += amount * _DENOM[m.group(2).lower()]
            found = True

        if not found:
            logger.warning(f"Could not coerce {field!r} to copper: {raw!r}")
            return None
        return total

    # ------------------------------------------------------------------
    # Enchantment parsing
    # ------------------------------------------------------------------

    def _suffix_to_int(self, suffix: str) -> Optional[int]:
        """Convert an enchantment suffix string to an integer.

        Handles signed numeric strings ("+5", "-2", "10") and Roman numerals
        ("VI" → 6). Returns None if the suffix is empty or unrecognised.
        """
        if not suffix:
            return None
        upper = suffix.upper()
        if upper in _ROMAN:
            return _ROMAN[upper]
        try:
            return int(suffix)
        except ValueError:
            return None

    def _strategy_colon(self, raw: str) -> Optional[Enchantment]:
        """Strategy 1: colon separator, e.g. ``"Resistance: +5"``."""
        if ": " not in raw:
            return None
        name, _, value_str = raw.partition(": ")
        return Enchantment(
            name=name.strip(), value=self._suffix_to_int(value_str.strip())
        )

    def _strategy_suffix_regex(self, raw: str) -> Optional[Enchantment]:
        """Strategy 2: trailing ``+N``/``-N`` or Roman-numeral suffix."""
        match = _ENCHANT_SUFFIX_RE.match(raw)
        if not match:
            return None
        name_part = match.group(1).strip()
        suffix = match.group(2).strip()
        # Validate Roman numerals against known set (avoids false matches on 'V' in names)
        if suffix.lstrip("+-").isdigit() or suffix.upper() in _ROMAN:
            return Enchantment(name=name_part, value=self._suffix_to_int(suffix))
        return None

    def _strategy_fallback(self, raw: str) -> Enchantment:
        """Strategy 3 (fallback): entire string becomes the name."""
        return Enchantment(name=raw, value=None)

    def _parse_enchantments(self, raw_list: list) -> list[Enchantment]:
        """Convert a list of raw enchantment strings to Enchantment models.

        Strategies are tried in order for each string; the first non-``None``
        result wins.  If all strategies return ``None``, the fallback is used.

        Args:
            raw_list: List of raw enchantment text strings from the parser

        Returns:
            List of Enchantment instances

        Example:
            >>> normalizer._parse_enchantments(["Resistance +5", "Superior Devotion VI"])
            [Enchantment(name='Resistance', value=5),
             Enchantment(name='Superior Devotion', value=6)]
        """
        strategies = [self._strategy_colon, self._strategy_suffix_regex]
        result: list[Enchantment] = []
        for raw in raw_list:
            raw = raw.strip()
            if not raw:
                continue
            for strategy in strategies:
                enchantment = strategy(raw)
                if enchantment is not None:
                    result.append(enchantment)
                    break
            else:
                result.append(self._strategy_fallback(raw))
        return result

    # ------------------------------------------------------------------
    # Sub-model parsers
    # ------------------------------------------------------------------

    def _parse_weapon_stats(self, fields: ParsedFields) -> Optional[WeaponStats]:
        """Build WeaponStats if any weapon field is present in the parsed dict.

        Args:
            fields: Raw fields dict from WikiPageParser

        Returns:
            WeaponStats instance or None if no weapon fields present

        Example:
            >>> fields = {"damage": "1d8+5", "critical_roll": "19-20/x2", ...}
            >>> normalizer._parse_weapon_stats(fields)
            WeaponStats(damage_dice='1d8', damage_bonus=5, ...)
        """
        weapon_keys = {
            "damage",
            "critical_roll",
            "handedness",
            "proficiency",
            "weapon_type",
            "enchantment_bonus",
        }
        if not weapon_keys.intersection(fields):
            return None

        damage_dice: Optional[str] = None
        damage_bonus: Optional[int] = None
        raw_damage = fields.get("damage")
        if raw_damage:
            m = _DAMAGE_RE.match(raw_damage)
            if m:
                damage_dice = m.group(1)
                if m.group(2):
                    damage_bonus = int(m.group(2))
            else:
                logger.warning(f"Could not parse damage string: {raw_damage!r}")

        critical_range: Optional[str] = None
        critical_multiplier: Optional[int] = None
        raw_crit = fields.get("critical_roll")
        if raw_crit:
            m = _CRIT_RE.search(raw_crit)
            if m:
                critical_range = m.group(1)
                critical_multiplier = int(m.group(2))
            else:
                logger.warning(f"Could not parse critical_roll string: {raw_crit!r}")

        damage_type = fields.get("damage_type", [])
        if isinstance(damage_type, str):
            damage_type = [v.strip() for v in damage_type.split(",") if v.strip()]

        return WeaponStats(
            damage_dice=damage_dice,
            damage_bonus=damage_bonus,
            damage_type=damage_type,
            critical_range=critical_range,
            critical_multiplier=critical_multiplier,
            enchantment_bonus=self._coerce_int(
                "enchantment_bonus", fields.get("enchantment_bonus")
            ),
            handedness=fields.get("handedness"),
            proficiency=fields.get("proficiency"),
            weapon_type=fields.get("weapon_type"),
        )

    def _parse_armor_stats(self, fields: ParsedFields) -> Optional[ArmorStats]:
        """Build ArmorStats if any armor field is present in the parsed dict.

        Args:
            fields: Raw fields dict from WikiPageParser

        Returns:
            ArmorStats instance or None if no armor fields present

        Example:
            >>> fields = {"armor_type": "Heavy", "armor_bonus": "9", ...}
            >>> normalizer._parse_armor_stats(fields)
            ArmorStats(armor_type='Heavy', armor_bonus=9, ...)
        """
        armor_keys = {
            "armor_type",
            "armor_bonus",
            "max_dex_bonus",
            "armor_check_penalty",
            "arcane_spell_failure",
        }
        if not armor_keys.intersection(fields):
            return None

        return ArmorStats(
            armor_type=fields.get("armor_type"),
            armor_bonus=self._coerce_int("armor_bonus", fields.get("armor_bonus")),
            max_dex_bonus=self._coerce_int(
                "max_dex_bonus", fields.get("max_dex_bonus")
            ),
            armor_check_penalty=self._coerce_int(
                "armor_check_penalty", fields.get("armor_check_penalty")
            ),
            arcane_spell_failure=self._coerce_percent(
                "arcane_spell_failure", fields.get("arcane_spell_failure")
            ),
        )

    def _parse_source(self, location_raw: Optional[str]) -> Optional[ItemSource]:
        """Build an ItemSource from the raw location field string.

        The wiki 'Location' field is free-form text. This method makes a
        best-effort extraction: quest names are comma-separated values.

        Args:
            location_raw: Raw location string from the infobox, or None

        Returns:
            ItemSource instance or None if no location data

        Example:
            >>> normalizer._parse_source("The Shroud, The Vision of Destruction")
            ItemSource(quests=['The Shroud', 'The Vision of Destruction'])
        """
        if not location_raw:
            return None

        quests = [q.strip() for q in location_raw.split(",") if q.strip()]
        return ItemSource(quests=quests)

    def _parse_named_set(self, raw: Optional[str]) -> Optional[NamedSet]:
        """Build a NamedSet from the raw 'named set' field string.

        Set bonuses are not present in the infobox field itself (they appear
        elsewhere on the wiki page and are not yet parsed), so bonuses will
        always be an empty list here.

        Args:
            raw: Raw set name string from the infobox, or None

        Returns:
            NamedSet instance or None if no set data

        Example:
            >>> normalizer._parse_named_set("Thelanis Fairy Tale")
            NamedSet(name='Thelanis Fairy Tale', bonuses=[])
        """
        if not raw:
            return None
        return NamedSet(name=raw.strip())
