# item_normalizer

Converts raw HTML from a DDO Wiki item page into a validated, fully typed `DDOItem` Pydantic model. The package is split into two layers:

- **`WikiPageParser`** — extracts raw string values from the HTML (no type coercion)
- **`ItemNormalizer`** — drives the parser and converts strings into their proper Python types

---

## Quick Start

```python
from item_normalizer import ItemNormalizer

normalizer = ItemNormalizer()
item = normalizer.normalize(html, wiki_url="https://ddowiki.com/page/Item:Sword_of_Shadow")

print(item.name)                   # "Sword of Shadow"
print(item.minimum_level)          # 20
print(item.base_value)             # 3620000  (copper pieces)
for e in item.enchantments:
    print(e.name, e.value)         # "Seeker", 4
```

---

## ItemNormalizer

### `normalize(html: str, wiki_url: str) → DDOItem`

Parses `html` and returns a validated `DDOItem`.

```python
normalizer = ItemNormalizer()
item = normalizer.normalize(html, wiki_url="https://ddowiki.com/page/Item:Foo")
```

**Error handling — three tiers:**

| Tier | Exception | Behaviour |
|---|---|---|
| Fatal | `ParseError` | HTML has no recognizable item structure; re-raised immediately |
| Field-level | *(logged)* | A single field could not be coerced; that field is set to `None` |
| Programming | `NormalizationError` | Pydantic rejected the assembled data; indicates a bug |

---

## Data Models

### DDOItem

The primary output. All fields except `name`, `wiki_url`, and `scraped_at` are optional.

```python
from item_normalizer import DDOItem
```

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Item name, stripped of the `Item:` wiki prefix |
| `item_type` | `str \| None` | e.g. `"Cloak"`, `"Longsword"` |
| `slot` | `str \| None` | Equipment slot (e.g. `"Back"`, `"Main Hand"`) |
| `minimum_level` | `int \| None` | Character level required to equip |
| `required_race` | `str \| None` | Race restriction if any |
| `required_class` | `str \| None` | Class restriction if any |
| `binding` | `str \| None` | e.g. `"Bound to Character on Acquire"` |
| `material` | `str \| None` | e.g. `"Cold Iron"`, `"Mithral"` |
| `hardness` | `int \| None` | Damage resistance value |
| `durability` | `int \| None` | Maximum durability points |
| `base_value` | `int \| None` | Sale value in copper pieces (see currency below) |
| `weight` | `float \| None` | Weight in pounds |
| `enchantments` | `list[Enchantment]` | Magical properties on the item |
| `weapon_stats` | `WeaponStats \| None` | Present only for weapons |
| `armor_stats` | `ArmorStats \| None` | Present only for armor/shields |
| `named_set` | `NamedSet \| None` | Set membership if applicable |
| `source` | `ItemSource \| None` | Where to obtain the item |
| `flavor_text` | `str \| None` | Italic lore text from the wiki page |
| `wiki_url` | `str` | Full URL of the source page |
| `scraped_at` | `datetime` | UTC timestamp of when data was fetched |

**Serialisation:**

```python
json_str = item.to_json()
restored = DDOItem.from_json(json_str)
```

### Enchantment

One magical property on an item.

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | e.g. `"Resistance"`, `"Seeker"`, `"Metalline"` |
| `value` | `int \| None` | Magnitude: `+5 → 5`, `-2 → -2`, `VI → 6`, passives → `None` |

Roman numeral tiers (I–XV) are converted to integers. Negative values are preserved.

### WeaponStats

Present on `DDOItem.weapon_stats` when the item is a weapon.

| Field | Type | Notes |
|---|---|---|
| `damage_dice` | `str \| None` | Dice notation, e.g. `"1d8"`, `"2d6"` |
| `damage_bonus` | `int \| None` | Flat damage bonus (e.g. `5` from `1d8+5`) |
| `damage_type` | `list[str]` | e.g. `["Slashing", "Magic"]` |
| `critical_range` | `str \| None` | Threat range, e.g. `"19-20"`, `"20"` |
| `critical_multiplier` | `int \| None` | Crit multiplier, e.g. `2`, `3` |
| `enchantment_bonus` | `int \| None` | Enhancement bonus (e.g. `5` for a +5 weapon) |
| `handedness` | `str \| None` | e.g. `"One-Handed"`, `"Two-Handed"` |
| `proficiency` | `str \| None` | Required proficiency group |
| `weapon_type` | `str \| None` | e.g. `"Longsword"`, `"Quarterstaff"` |

### ArmorStats

Present on `DDOItem.armor_stats` when the item is armor or a shield.

| Field | Type | Notes |
|---|---|---|
| `armor_type` | `str \| None` | `"Light"`, `"Medium"`, `"Heavy"`, `"Shield"`, `"Docent"` |
| `armor_bonus` | `int \| None` | AC bonus granted |
| `max_dex_bonus` | `int \| None` | Maximum DEX modifier while wearing |
| `armor_check_penalty` | `int \| None` | Penalty applied to physical skill checks |
| `arcane_spell_failure` | `int \| None` | Percentage chance arcane spells fail |

### NamedSet

Set membership information.

| Field | Type |
|---|---|
| `name` | `str` |
| `bonuses` | `list[SetBonus]` |

### SetBonus

| Field | Type |
|---|---|
| `pieces_required` | `int` |
| `description` | `str` |

### ItemSource

| Field | Type |
|---|---|
| `quests` | `list[str]` |

---

## Currency

`base_value` is stored as total **copper pieces (cp)**:

| Denomination | cp value |
|---|---|
| 1 pp (platinum) | 1,000 cp |
| 1 gp (gold) | 100 cp |
| 1 sp (silver) | 10 cp |
| 1 cp (copper) | 1 cp |

Example: `3,620 pp` → `3,620,000` cp.

Compound values like `3 pp, 2 gp, 5 sp, 8 cp` are also handled correctly.

---

## HTML Parsing Details

`WikiPageParser` extracts data from DDO Wiki's MediaWiki HTML structure:

| Data | Strategy |
|---|---|
| Item name | `<h1 id="firstHeading">`, strips `Item:` prefix; falls back to `<th colspan="2">` in wikitable |
| Infobox fields | `<th>`/`<td>` rows inside all `<table>` elements; keys normalised via `FIELD_ALIASES` |
| Enchantments | `<th>Enchantments</th>` in infobox → adjacent `<td>` → `<ul>/<li>`; falls back to `<b>Enchantments</b>` label |
| Flavor text | First `<i>` tag > 20 characters that contains no links |

**Tooltip stripping:** DDO Wiki nests `<span class="popup tooltip">` description spans inside visible text, and `<span class="sortkey">` zero-padded sort keys inside table cells. Both are stripped before any text extraction to prevent duplication.

---

## Field Aliases

The parser maps wiki infobox header text to canonical field names. Common synonyms handled:

| Wiki text | Canonical field |
|---|---|
| `Minimum Level`, `Min Level` | `minimum_level` |
| `Race Absolutely Required`, `Required Race` | `required_race` |
| `Equips To`, `Slot` | `slot` |
| `Made From`, `Material` | `material` |
| `Damage and Type`, `Damage` | `damage` |
| `Critical Threat Range`, `Critical Roll` | `critical_roll` |
| `Enhancement Bonus`, `Enchantment Bonus` | `enchantment_bonus` |
| `Proficiency Class`, `Proficiency` | `proficiency` |

---

## Exceptions

All exceptions inherit from `ItemNormalizerError`.

| Exception | When raised |
|---|---|
| `ItemNormalizerError` | Base class |
| `ParseError` | HTML structure unrecognizable (no item name, no wikitable) |
| `NormalizationError` | Pydantic rejected the assembled `DDOItem` — indicates a code bug |

```python
from item_normalizer import ParseError, NormalizationError

try:
    item = normalizer.normalize(html, wiki_url=url)
except ParseError as e:
    print(f"Could not parse {e.field}: {e}")
except NormalizationError as e:
    print(f"Data assembly bug: {e}")
```
