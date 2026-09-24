"""Unit tests for item_normalizer.parser.WikiPageParser."""

import pytest

from item_normalizer.exceptions import ParseError
from item_normalizer.parser import WikiPageParser

WIKI_URL = "https://ddowiki.com/page/Item:Test"


@pytest.fixture
def parser() -> WikiPageParser:
    return WikiPageParser()


# ---------------------------------------------------------------------------
# Name extraction
# ---------------------------------------------------------------------------


class TestExtractName:
    def test_from_h1_firstheading(self, parser: WikiPageParser) -> None:
        html = '<h1 id="firstHeading">Item:Mantle of the Worldshaper</h1>'
        fields = parser.parse(html, WIKI_URL)
        assert fields["name"] == "Mantle of the Worldshaper"

    def test_strips_item_prefix(self, parser: WikiPageParser) -> None:
        html = '<h1 id="firstHeading">Item:Sword of Shadow</h1>'
        fields = parser.parse(html, WIKI_URL)
        assert fields["name"] == "Sword of Shadow"

    def test_fallback_to_wikitable_th(self, parser: WikiPageParser) -> None:
        html = """
        <table class="wikitable">
          <tr><th colspan="2">Mantle of the Worldshaper</th></tr>
          <tr><th>Minimum Level</th><td>15</td></tr>
        </table>
        """
        fields = parser.parse(html, WIKI_URL)
        assert fields["name"] == "Mantle of the Worldshaper"

    def test_raises_parse_error_when_no_name(self, parser: WikiPageParser) -> None:
        html = "<html><body><p>No item here</p></body></html>"
        with pytest.raises(ParseError, match="Could not extract item name"):
            parser.parse(html, WIKI_URL)

    def test_raises_parse_error_when_h1_is_only_item_prefix(
        self, parser: WikiPageParser
    ) -> None:
        """h1 containing only 'Item:' leaves an empty name; falls through to ParseError."""
        html = '<h1 id="firstHeading">Item:</h1>'
        with pytest.raises(ParseError, match="Could not extract item name"):
            parser.parse(html, WIKI_URL)

    def test_raises_parse_error_when_no_h1_and_no_wikitable(
        self, parser: WikiPageParser
    ) -> None:
        """No h1 and no wikitable both absent triggers ParseError."""
        html = (
            "<html><body><table><tr><td>Not a wikitable</td></tr></table></body></html>"
        )
        with pytest.raises(ParseError, match="Could not extract item name"):
            parser.parse(html, WIKI_URL)


# ---------------------------------------------------------------------------
# Infobox field extraction
# ---------------------------------------------------------------------------


class TestExtractInfoboxFields:
    def test_minimum_level(self, parser: WikiPageParser, cloak_html: str) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        assert fields["minimum_level"] == "20"

    def test_binding(self, parser: WikiPageParser, cloak_html: str) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        assert fields["binding"] == "Bound to Character on Acquire"

    def test_material(self, parser: WikiPageParser, cloak_html: str) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        assert fields["material"] == "Cloth"

    def test_hardness_and_durability(
        self, parser: WikiPageParser, cloak_html: str
    ) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        assert fields["hardness"] == "14"
        assert fields["durability"] == "100"

    def test_weight(self, parser: WikiPageParser, cloak_html: str) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        assert fields["weight"] == "0.1 lbs"

    def test_slot_alias_equips_to(
        self, parser: WikiPageParser, cloak_html: str
    ) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        assert fields["slot"] == "Back"

    def test_damage_type_is_list(
        self, parser: WikiPageParser, weapon_html: str
    ) -> None:
        fields = parser.parse(weapon_html, WIKI_URL)
        assert isinstance(fields["damage_type"], list)
        assert "Slashing" in fields["damage_type"]
        assert "Magic" in fields["damage_type"]

    def test_critical_roll_alias(
        self, parser: WikiPageParser, weapon_html: str
    ) -> None:
        fields = parser.parse(weapon_html, WIKI_URL)
        assert "critical_roll" in fields
        assert "19-20" in fields["critical_roll"]

    def test_enhancement_bonus_alias(
        self, parser: WikiPageParser, weapon_html: str
    ) -> None:
        fields = parser.parse(weapon_html, WIKI_URL)
        assert fields["enchantment_bonus"] == "5"

    def test_armor_type(self, parser: WikiPageParser, armor_html: str) -> None:
        fields = parser.parse(armor_html, WIKI_URL)
        assert fields["armor_type"] == "Heavy"

    def test_armor_check_penalty(self, parser: WikiPageParser, armor_html: str) -> None:
        fields = parser.parse(armor_html, WIKI_URL)
        assert fields["armor_check_penalty"] == "-3"

    def test_arcane_spell_failure(
        self, parser: WikiPageParser, armor_html: str
    ) -> None:
        fields = parser.parse(armor_html, WIKI_URL)
        assert fields["arcane_spell_failure"] == "25%"

    def test_named_set_raw(self, parser: WikiPageParser, cloak_html: str) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        assert fields["named_set_raw"] == "Thelanis Fairy Tale"

    def test_unknown_field_ignored(self, parser: WikiPageParser) -> None:
        html = """
        <h1 id="firstHeading">Item:Test</h1>
        <table class="wikitable">
          <tr><th>Unknown Field XYZ</th><td>some value</td></tr>
          <tr><th>Minimum Level</th><td>5</td></tr>
        </table>
        """
        fields = parser.parse(html, WIKI_URL)
        assert "unknown_field_xyz" not in fields
        assert fields["minimum_level"] == "5"

    def test_no_infobox_yields_warning_not_error(
        self, parser: WikiPageParser, minimal_html: str
    ) -> None:
        fields = parser.parse(minimal_html, WIKI_URL)
        assert fields["name"] == "Basic Ring"
        assert fields["enchantments"] == []

    def test_row_without_th_and_td_is_skipped(self, parser: WikiPageParser) -> None:
        """Rows containing only a th (no td) or only a td are silently skipped."""
        html = """
        <h1 id="firstHeading">Item:Test Item</h1>
        <table class="wikitable">
          <tr><th colspan="2">Test Item</th></tr>
          <tr><td>Cell with no header</td></tr>
          <tr><th>Minimum Level</th><td>10</td></tr>
        </table>
        """
        fields = parser.parse(html, WIKI_URL)
        # The row with only a td is skipped; the valid row is parsed normally.
        assert fields["minimum_level"] == "10"


# ---------------------------------------------------------------------------
# Enchantment extraction
# ---------------------------------------------------------------------------


class TestExtractEnchantments:
    def test_cloak_enchantments(self, parser: WikiPageParser, cloak_html: str) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        enchants = fields["enchantments"]
        assert "Superior Devotion VI" in enchants
        assert "Resistance +5" in enchants
        assert "Metalline" in enchants

    def test_no_enchantments_returns_empty_list(
        self, parser: WikiPageParser, minimal_html: str
    ) -> None:
        fields = parser.parse(minimal_html, WIKI_URL)
        assert fields["enchantments"] == []

    def test_weapon_enchantments(
        self, parser: WikiPageParser, weapon_html: str
    ) -> None:
        fields = parser.parse(weapon_html, WIKI_URL)
        assert "Vorpal" in fields["enchantments"]

    def test_enchantments_via_infobox_th_td(self, parser: WikiPageParser) -> None:
        """Enchantments stored in a th/td infobox row are extracted via strategy 1."""
        html = """
        <h1 id="firstHeading">Item:Test Item</h1>
        <table class="wikitable">
          <tr><th>Enchantments</th><td><ul><li>Vorpal</li><li>Improved Critical</li></ul></td></tr>
        </table>
        """
        fields = parser.parse(html, WIKI_URL)
        assert "Vorpal" in fields["enchantments"]
        assert "Improved Critical" in fields["enchantments"]

    def test_enchantments_bold_label_with_no_ul_returns_empty(
        self, parser: WikiPageParser
    ) -> None:
        """A bold 'Enchantments' label with no following <ul> returns an empty list."""
        html = """
        <h1 id="firstHeading">Item:Test Item</h1>
        <p><b>Enchantments:</b></p>
        <p>Some unrelated paragraph without a list.</p>
        """
        fields = parser.parse(html, WIKI_URL)
        assert fields["enchantments"] == []


# ---------------------------------------------------------------------------
# Flavor text extraction
# ---------------------------------------------------------------------------


class TestExtractFlavorText:
    def test_cloak_flavor_text(self, parser: WikiPageParser, cloak_html: str) -> None:
        fields = parser.parse(cloak_html, WIKI_URL)
        expected = "This cloak was woven from the fabric of the planes themselves."
        assert fields["flavor_text"] == expected

    def test_no_flavor_text_returns_none(
        self, parser: WikiPageParser, weapon_html: str
    ) -> None:
        fields = parser.parse(weapon_html, WIKI_URL)
        assert fields.get("flavor_text") is None

    def test_armor_flavor_text(self, parser: WikiPageParser, armor_html: str) -> None:
        fields = parser.parse(armor_html, WIKI_URL)
        assert "celestial" in fields["flavor_text"]

    def test_italic_with_link_is_skipped(self, parser: WikiPageParser) -> None:
        """An <i> tag containing a hyperlink is not treated as flavor text."""
        html = """
        <h1 id="firstHeading">Item:Test Item</h1>
        <div class="mw-parser-output">
          <i><a href="/wiki/Something">This italic has a link and is long enough to pass</a></i>
          <i>This is the real flavor text that is long enough to qualify here.</i>
        </div>
        """
        fields = parser.parse(html, WIKI_URL)
        assert fields.get("flavor_text") == (
            "This is the real flavor text that is long enough to qualify here."
        )

    def test_short_italic_text_is_skipped(self, parser: WikiPageParser) -> None:
        """An <i> tag whose text is 20 characters or fewer is not flavor text."""
        html = """
        <h1 id="firstHeading">Item:Test Item</h1>
        <div class="mw-parser-output">
          <i>Too short.</i>
        </div>
        """
        fields = parser.parse(html, WIKI_URL)
        assert fields.get("flavor_text") is None


# ---------------------------------------------------------------------------
# wiki_url injection
# ---------------------------------------------------------------------------


class TestWikiUrl:
    def test_wiki_url_is_injected(
        self, parser: WikiPageParser, cloak_html: str
    ) -> None:
        url = "https://ddowiki.com/page/Item:Mantle_of_the_Worldshaper"
        fields = parser.parse(cloak_html, url)
        assert fields["wiki_url"] == url


# ---------------------------------------------------------------------------
# No infobox table — warning logged but no error (line 145)
# ---------------------------------------------------------------------------


class TestNoInfoboxTable:
    def test_name_extracted_but_no_table_fields(self, parser: WikiPageParser) -> None:
        """An h1 name with no <table> element logs a warning and returns minimal fields."""
        html = "<html><body><h1 id='firstHeading'>Item:TestSword</h1></body></html>"
        fields = parser.parse(html, WIKI_URL)
        assert fields["name"] == "TestSword"
        assert fields["enchantments"] == []
        # No infobox fields should be present
        assert "minimum_level" not in fields


# ---------------------------------------------------------------------------
# Tooltip and sortkey span stripping (lines 163, 167)
# ---------------------------------------------------------------------------


class TestStripTooltipSpans:
    def test_tooltip_span_text_is_removed(self, parser: WikiPageParser) -> None:
        """Tooltip spans are decomposed so their text does not pollute field values."""
        html = """
        <html><body>
        <h1 id="firstHeading">Item:TestSword</h1>
        <table class="wikitable">
        <tr><th colspan="2">TestSword</th></tr>
        <tr><th>Minimum Level</th><td>15<span class="tooltip">level 15</span></td></tr>
        </table>
        </body></html>
        """
        fields = parser.parse(html, WIKI_URL)
        # The tooltip text "level 15" must not appear in the extracted value
        assert fields["minimum_level"] == "15"
        assert "level 15" not in fields["minimum_level"]

    def test_sortkey_span_text_is_removed(self, parser: WikiPageParser) -> None:
        """Sortkey spans are decomposed so their zero-padded numbers are excluded."""
        html = """
        <html><body>
        <h1 id="firstHeading">Item:TestSword</h1>
        <table class="wikitable">
        <tr><th colspan="2">TestSword</th></tr>
        <tr><th>Base Value</th><td><span class="sortkey">0003620000</span>3,620 pp</td></tr>
        </table>
        </body></html>
        """
        fields = parser.parse(html, WIKI_URL)
        assert "0003620000" not in fields["base_value"]
        assert "3,620 pp" in fields["base_value"]


# ---------------------------------------------------------------------------
# _extract_name — wikitable fallback branches (210->215, 212->215)
# ---------------------------------------------------------------------------


class TestExtractNameWikitableFallbacks:
    def test_wikitable_with_no_colspan_th_raises_parse_error(
        self, parser: WikiPageParser
    ) -> None:
        """Wikitable exists but has no <th colspan=...> → ParseError (branch 210->215)."""
        html = """
        <html><body>
        <table class="wikitable"><tr><td>just a cell</td></tr></table>
        </body></html>
        """
        with pytest.raises(ParseError, match="Could not extract item name"):
            parser.parse(html, WIKI_URL)

    def test_wikitable_with_empty_colspan_th_raises_parse_error(
        self, parser: WikiPageParser
    ) -> None:
        """Wikitable with a blank <th colspan="2"> → ParseError (branch 212->215)."""
        html = """
        <html><body>
        <table class="wikitable"><tr><th colspan="2">  </th></tr></table>
        </body></html>
        """
        with pytest.raises(ParseError, match="Could not extract item name"):
            parser.parse(html, WIKI_URL)


# ---------------------------------------------------------------------------
# _extract_enchantments — infobox th/td edge cases (branches 317, 319, 321)
# ---------------------------------------------------------------------------


class TestExtractEnchantmentsEdgeCases:
    def test_enchantments_th_with_no_td_returns_empty(
        self, parser: WikiPageParser
    ) -> None:
        """Enchantments <th> with no <td> in its <tr> falls through to strategy 2."""
        html = """
        <html><body>
        <h1 id="firstHeading">Item:TestSword</h1>
        <table class="wikitable">
        <tr><th colspan="2">TestSword</th></tr>
        <tr><th>Enchantments</th></tr>
        </table>
        </body></html>
        """
        fields = parser.parse(html, WIKI_URL)
        assert fields["enchantments"] == []

    def test_enchantments_th_td_with_no_ul_returns_empty(
        self, parser: WikiPageParser
    ) -> None:
        """Enchantments <td> present but contains no <ul> falls through (branch 321->314)."""
        html = """
        <html><body>
        <h1 id="firstHeading">Item:TestSword</h1>
        <table class="wikitable">
        <tr><th colspan="2">TestSword</th></tr>
        <tr><th>Enchantments</th><td>Some text but no ul</td></tr>
        </table>
        </body></html>
        """
        fields = parser.parse(html, WIKI_URL)
        assert fields["enchantments"] == []
