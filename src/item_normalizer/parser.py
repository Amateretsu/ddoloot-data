"""HTML parser for DDO Wiki item pages.

Extracts raw field values from MediaWiki-rendered HTML and returns a plain
dict of string values. No type coercion is performed here — the normalizer
is responsible for converting strings to their proper Python types.

Example:
    >>> from item_normalizer.parser import WikiPageParser
    >>> parser = WikiPageParser()
    >>> fields = parser.parse(html, wiki_url="https://ddowiki.com/page/Item:Foo")
    >>> fields["name"]
    'Foo'
    >>> fields["minimum_level"]
    '15'
"""

import re
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag
from loguru import logger

from item_normalizer.exceptions import ParseError

# Maps lowercased, colon-stripped infobox <th> text to canonical field names.
# Multiple wiki phrasings can map to the same canonical name.
FIELD_ALIASES: dict[str, str] = {
    "minimum level": "minimum_level",
    "min level": "minimum_level",
    "required race": "required_race",
    "race absolutely required": "required_race",
    "required class": "required_class",
    "binding": "binding",
    "item type": "item_type",
    "equips to": "slot",
    "slot": "slot",
    "material": "material",
    "made from": "material",
    "hardness": "hardness",
    "durability": "durability",
    "base value": "base_value",
    "weight": "weight",
    "damage": "damage",
    "damage and type": "damage",
    "damage type": "damage_type",
    "critical roll": "critical_roll",
    "critical threat range": "critical_roll",
    "enchantment bonus": "enchantment_bonus",
    "enhancement bonus": "enchantment_bonus",
    "handedness": "handedness",
    "proficiency": "proficiency",
    "proficiency class": "proficiency",
    "weapon type": "weapon_type",
    "armor type": "armor_type",
    "armor bonus": "armor_bonus",
    "maximum dex bonus": "max_dex_bonus",
    "max dex bonus": "max_dex_bonus",
    "armor check penalty": "armor_check_penalty",
    "arcane spell failure": "arcane_spell_failure",
    "location": "location",
    "named set": "named_set_raw",
    "flavor text": "flavor_text",
    "description": "flavor_text",
}

# Field names that are expected to contain comma-separated lists.
LIST_FIELDS: frozenset[str] = frozenset({"damage_type"})

# Roman numerals used as enchantment tier indicators in DDO.
ROMAN_NUMERALS: frozenset[str] = frozenset(
    {
        "I",
        "II",
        "III",
        "IV",
        "V",
        "VI",
        "VII",
        "VIII",
        "IX",
        "X",
        "XI",
        "XII",
        "XIII",
        "XIV",
        "XV",
    }
)

# Type alias for the intermediate parsed dict returned by WikiPageParser.
ParsedFields = dict[str, Any]


class WikiPageParser:
    """Extracts raw field values from a DDO Wiki item page HTML string.

    The parser targets the MediaWiki HTML structure used by ddowiki.com:
    - Item name from <h1 id="firstHeading"> or the wikitable header row
    - Infobox fields from nested <th>/<td> table rows
    - Enchantments from a <ul> list following a <b>Enchantments:</b> label
    - Flavor text from the first substantial <i> tag in the content area

    Raises:
        ParseError: If the HTML has no recognizable item structure
                    (no h1 heading and no wikitable found)

    Example:
        >>> parser = WikiPageParser()
        >>> fields = parser.parse(html, wiki_url="https://ddowiki.com/page/Item:Foo")
        >>> fields["name"]
        'Foo'
    """

    def parse(self, html: str, wiki_url: str) -> ParsedFields:
        """Parse an item page HTML string into a raw field dict.

        Args:
            html: Raw HTML string from WikiFetcher
            wiki_url: The source URL, injected verbatim into the result

        Returns:
            Dict mapping canonical field names to raw string (or list) values.
            Always contains at minimum: 'name', 'wiki_url', 'enchantments'.

        Raises:
            ParseError: If neither h1 nor wikitable can be found

        Example:
            >>> fields = parser.parse(html, wiki_url="https://ddowiki.com/page/Item:Foo")
            >>> fields["wiki_url"]
            'https://ddowiki.com/page/Item:Foo'
        """
        soup = self._get_soup(html)
        self._strip_tooltips(soup)

        name = self._extract_name(soup)
        logger.debug(f"Parsed item name: {name!r}")

        tables = self._find_tables(soup)
        raw_fields: ParsedFields = {"name": name, "wiki_url": wiki_url}

        if tables is not None:
            raw_fields.update(self._extract_infobox_fields(tables))
        else:
            logger.warning(f"No infobox table found for item: {name!r}")

        raw_fields["enchantments"] = self._extract_enchantments(soup)
        raw_fields.setdefault("flavor_text", self._extract_flavor_text(soup))

        return raw_fields

    def _strip_tooltips(self, soup: BeautifulSoup) -> None:
        """Remove popup tooltip spans so their text doesn't pollute field values.

        DDO Wiki wraps many values in:
            <span class="popup has_tooltip">visible text
                <span class="popup tooltip">full description...</span>
            </span>
        BeautifulSoup's get_text() captures both spans, doubling the text.
        Decomposing the inner tooltip span before any extraction prevents this.
        """
        for span in soup.find_all("span", class_="tooltip"):
            span.decompose()
        # Sortkey spans hold zero-padded numeric sort values (e.g. "0003620000")
        # that pollute field text when captured by get_text().
        for span in soup.find_all("span", class_="sortkey"):
            span.decompose()

    def _get_soup(self, html: str) -> BeautifulSoup:
        """Parse HTML with the stdlib html.parser.

        Args:
            html: Raw HTML string

        Returns:
            Parsed BeautifulSoup object
        """
        return BeautifulSoup(html, "html.parser")

    def _extract_name(self, soup: BeautifulSoup) -> str:
        """Extract the item name from the page.

        Strategy 1 (preferred): <h1 id="firstHeading"> — strips the 'Item:' prefix.
        Strategy 2 (fallback): <th colspan="2"> inside the main wikitable.

        Args:
            soup: Parsed BeautifulSoup object

        Returns:
            Item name as a plain string

        Raises:
            ParseError: If neither strategy yields a non-empty name

        Example:
            >>> # <h1 id="firstHeading">Item:Mantle of the Worldshaper</h1>
            >>> parser._extract_name(soup)
            'Mantle of the Worldshaper'
        """
        h1 = soup.find("h1", id="firstHeading")
        if h1:
            text = h1.get_text(strip=True)
            name = re.sub(r"^Item:", "", text).strip()
            if name:
                return name

        table = soup.find("table", class_="wikitable")
        if table:
            header_th = table.find("th", attrs={"colspan": True})
            if header_th:
                name = header_th.get_text(strip=True)
                if name:
                    return name

        raise ParseError(
            "Could not extract item name: no <h1 id='firstHeading'> or wikitable header found",
            field="name",
        )

    def _find_tables(self, soup: BeautifulSoup) -> Optional[Tag]:
        """Locate the main item infobox table.

        DDO Wiki item pages have one primary wikitable. The infobox fields
        live inside a nested table within that outer table.

        Args:
            soup: Parsed BeautifulSoup object

        Returns:
            The outer wikitable Tag, or None if not found

        Example:
            >>> infobox = parser._find_tables(soup)
            >>> infobox is not None
            True
        """
        has_wikitable = bool(soup.find_all("table", class_="wikitable"))
        logger.debug(f"Found table(s): {has_wikitable}")
        return soup.find_all("table")

    def _extract_infobox_fields(self, tables: list) -> ParsedFields:
        """Walk every <th>/<td> row in the infobox and return canonical field values.

        The infobox key (from <th>) is lowercased, stripped of trailing colons
        and whitespace, then looked up in FIELD_ALIASES. Unknown keys are logged
        at DEBUG level and skipped.

        Fields listed in LIST_FIELDS are split on commas into a list of strings.

        Args:
            infobox: The wikitable Tag containing infobox rows

        Returns:
            Dict of canonical field name -> raw string (or list[str]) value

        Example:
            >>> fields = parser._extract_infobox_fields(infobox)
            >>> fields["minimum_level"]
            '15'
            >>> fields["damage_type"]
            ['Slashing', 'Magic']
        """
        fields: ParsedFields = {}
        cnt = 0

        logger.debug(f"Processing {len(tables)} table(s)...")
        for table in tables:
            cnt += 1
            row_count = len(table.find_all("tr"))
            logger.debug(f"Found {row_count} row(s) in table {cnt}.")
            for row in table.find_all("tr"):
                th = row.find("th")
                td = row.find("td")

                if not th or not td:
                    continue

                raw_key = th.get_text(strip=True).rstrip(":").strip().lower()
                canonical = FIELD_ALIASES.get(raw_key)

                if canonical is None:
                    logger.warning(f"Unknown infobox field ignored: {raw_key!r}")
                    continue

                raw_value = td.get_text(separator=" ", strip=True)

                if canonical in LIST_FIELDS:
                    fields[canonical] = [
                        v.strip() for v in raw_value.split(",") if v.strip()
                    ]
                else:
                    fields[canonical] = raw_value

        return fields

    def _extract_enchantments(self, soup: BeautifulSoup) -> list[str]:
        """Find the Enchantments section and return a list of raw li text values.

        Looks for a <b> tag containing 'Enchantment' (case-insensitive), then
        finds the nearest following <ul> and collects all <li> text.

        Args:
            soup: Parsed BeautifulSoup object

        Returns:
            List of raw enchantment strings (e.g. ['Resistance +5', 'Metalline'])
            Returns an empty list if no enchantments section is found.

        Example:
            >>> parser._extract_enchantments(soup)
            ['Superior Devotion VI', 'Resistance +5']
        """
        # Strategy 1: infobox <th>Enchantments</th> → sibling <td> → <ul>
        for th in soup.find_all("th"):
            if re.fullmatch(r"enchantments?", th.get_text(strip=True), re.IGNORECASE):
                row = th.find_parent("tr")
                if row:
                    td = row.find("td")
                    if td:
                        ul = td.find("ul")
                        if ul:
                            items = self._extract_li_texts(ul)
                            logger.debug(
                                f"Extracted {len(items)} enchantments (infobox th/td strategy)"
                            )
                            return items

        # Strategy 2: standalone <b>/<strong> label followed by a <ul>
        enchant_label = soup.find(
            ["b", "strong"],
            string=re.compile(r"enchantment", re.IGNORECASE),
        )

        if enchant_label is None:
            logger.debug("No enchantments section found on page")
            return []

        ul = enchant_label.find_next("ul")
        if ul is None:
            logger.debug("Enchantments label found but no following <ul>")
            return []

        items = self._extract_li_texts(ul)
        logger.debug(f"Extracted {len(items)} enchantments (bold label strategy)")
        return items

    def _extract_li_texts(self, ul: Tag) -> list[str]:
        """Return non-empty text strings for every <li> in a <ul>."""
        return [
            li.get_text(separator=" ", strip=True)
            for li in ul.find_all("li")
            if li.get_text(strip=True)
        ]

    def _extract_flavor_text(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract the item's flavor/lore text from the first qualifying italic tag.

        Heuristic: the first <i> tag in the main content area whose text is
        longer than 20 characters and does not contain a link anchor.

        Args:
            soup: Parsed BeautifulSoup object

        Returns:
            Flavor text string, or None if not found

        Example:
            >>> parser._extract_flavor_text(soup)
            'This cloak was woven from the fabric of the planes themselves.'
        """
        content = soup.find("div", class_="mw-parser-output") or soup

        for tag in content.find_all("i"):
            if tag.find("a"):
                continue
            text = tag.get_text(strip=True)
            if len(text) > 20:
                logger.debug(f"Flavor text found: {text[:60]!r}...")
                return text

        return None
