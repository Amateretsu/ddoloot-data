"""Shared HTML fixtures for item_normalizer tests.

Provides realistic but minimal MediaWiki HTML snippets for four item archetypes:
  - cloak_html    : accessory with enchantments and a named set
  - weapon_html   : longsword with full weapon stats
  - armor_html    : heavy armor with all armor fields
  - minimal_html  : bare-minimum item (name only, no infobox)
"""

import pytest


@pytest.fixture
def cloak_html() -> str:
    """A cloak item page with enchantments, named set, and flavor text."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Item:Mantle of the Worldshaper - DDO wiki</title></head>
    <body>
    <div id="mw-content-text">
      <h1 id="firstHeading">Item:Mantle of the Worldshaper</h1>
      <div class="mw-parser-output">
        <table class="wikitable">
          <tr><th colspan="2">Mantle of the Worldshaper</th></tr>
          <tr><th>Item Type</th><td>Cloak</td></tr>
          <tr><th>Equips To</th><td>Back</td></tr>
          <tr><th>Minimum Level</th><td>20</td></tr>
          <tr><th>Binding</th><td>Bound to Character on Acquire</td></tr>
          <tr><th>Material</th><td>Cloth</td></tr>
          <tr><th>Hardness</th><td>14</td></tr>
          <tr><th>Durability</th><td>100</td></tr>
          <tr><th>Base Value</th><td>12,650 gp</td></tr>
          <tr><th>Weight</th><td>0.1 lbs</td></tr>
          <tr><th>Named Set</th><td>Thelanis Fairy Tale</td></tr>
          <tr><th>Location</th><td>The Snitch, The Spinner of Shadows</td></tr>
        </table>
        <p><b>Enchantments:</b></p>
        <ul>
          <li>Superior Devotion VI</li>
          <li>Resistance +5</li>
          <li>Metalline</li>
          <li>Good Luck +2</li>
        </ul>
        <p><i>This cloak was woven from the fabric of the planes themselves.</i></p>
      </div>
    </div>
    </body>
    </html>
    """


@pytest.fixture
def weapon_html() -> str:
    """A longsword item page with full weapon combat stats."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Item:Sword of Shadow - DDO wiki</title></head>
    <body>
    <div id="mw-content-text">
      <h1 id="firstHeading">Item:Sword of Shadow</h1>
      <div class="mw-parser-output">
        <table class="wikitable">
          <tr><th colspan="2">Sword of Shadow</th></tr>
          <tr><th>Item Type</th><td>Weapon</td></tr>
          <tr><th>Weapon Type</th><td>Longsword</td></tr>
          <tr><th>Equips To</th><td>Main Hand</td></tr>
          <tr><th>Minimum Level</th><td>15</td></tr>
          <tr><th>Binding</th><td>Bound to Character on Equip</td></tr>
          <tr><th>Handedness</th><td>One-Handed</td></tr>
          <tr><th>Proficiency</th><td>Martial Weapon Proficiency</td></tr>
          <tr><th>Damage</th><td>1d8+5</td></tr>
          <tr><th>Damage Type</th><td>Slashing, Magic</td></tr>
          <tr><th>Critical Roll</th><td>19-20/x2</td></tr>
          <tr><th>Enhancement Bonus</th><td>5</td></tr>
          <tr><th>Hardness</th><td>18</td></tr>
          <tr><th>Durability</th><td>150</td></tr>
          <tr><th>Base Value</th><td>8,400 gp</td></tr>
          <tr><th>Weight</th><td>4 lbs</td></tr>
          <tr><th>Location</th><td>The Pit</td></tr>
        </table>
        <p><b>Enchantments:</b></p>
        <ul>
          <li>Vorpal</li>
          <li>Improved Critical: Slashing Weapons</li>
          <li>Shadowstrike +3</li>
        </ul>
      </div>
    </div>
    </body>
    </html>
    """


@pytest.fixture
def armor_html() -> str:
    """A heavy armor item page with all armor-specific fields."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Item:Plate of the Fallen - DDO wiki</title></head>
    <body>
    <div id="mw-content-text">
      <h1 id="firstHeading">Item:Plate of the Fallen</h1>
      <div class="mw-parser-output">
        <table class="wikitable">
          <tr><th colspan="2">Plate of the Fallen</th></tr>
          <tr><th>Item Type</th><td>Armor</td></tr>
          <tr><th>Armor Type</th><td>Heavy</td></tr>
          <tr><th>Equips To</th><td>Body</td></tr>
          <tr><th>Minimum Level</th><td>18</td></tr>
          <tr><th>Binding</th><td>Bound to Character on Acquire</td></tr>
          <tr><th>Material</th><td>Mithral</td></tr>
          <tr><th>Armor Bonus</th><td>9</td></tr>
          <tr><th>Maximum Dex Bonus</th><td>3</td></tr>
          <tr><th>Armor Check Penalty</th><td>-3</td></tr>
          <tr><th>Arcane Spell Failure</th><td>25%</td></tr>
          <tr><th>Hardness</th><td>20</td></tr>
          <tr><th>Durability</th><td>200</td></tr>
          <tr><th>Base Value</th><td>24,000 gp</td></tr>
          <tr><th>Weight</th><td>50 lbs</td></tr>
        </table>
        <p><b>Enchantments:</b></p>
        <ul>
          <li>Greater Fortification</li>
          <li>Fortification +150%</li>
        </ul>
        <p><i>Forged from the armor of a fallen celestial, it pulses with divine energy.</i></p>
      </div>
    </div>
    </body>
    </html>
    """


@pytest.fixture
def minimal_html() -> str:
    """A bare-minimum item page — name only, no infobox, no enchantments."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Item:Basic Ring - DDO wiki</title></head>
    <body>
    <div id="mw-content-text">
      <h1 id="firstHeading">Item:Basic Ring</h1>
      <div class="mw-parser-output">
        <p>This item has no recorded stats.</p>
      </div>
    </div>
    </body>
    </html>
    """
