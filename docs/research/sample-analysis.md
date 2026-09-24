# Sample page analysis vs the original normalizer

Sample: 40 item pages, one per update (Update 5 to 79), cached at `cache/html/` (gitignored, fetched via the ADR 0006 browser fallback). Compared against the original normalizer package (parser + normalizer) as copied from the app repo. That package has since been deleted; `src/item_extractor` is now the only HTML → Scraped Item module.

## Page templates found

Every page has one main infobox table inside `div.mw-parser-output`, but the row set depends on the item's template. Five templates appear, identified by the first row label:

| Template | First row | Pages | Distinguishing rows |
|---|---|---|---|
| Accessory (jewelry/clothing) | Minimum Level | 18 | `Item Type` ("Jewelry / Ring"), `Slot` |
| Armor | Armor Type | 10 | Armor Bonus, Max Dex, ACP, ASF, Feat Requirement; cosmetic/docent variants |
| Weapon | Proficiency Class | 8 | Damage and Type, Critical threat range, Weapon Type, Handedness, Attack/Damage Mod, UMD DC, Accepts Sentience? |
| Shield / orb | Shield Type | 2 | Shield Bonus, Damage Reduction, plus weapon-style damage rows; enchantments labelled **Enhancements** |
| Accessory without type/slot | Minimum Level | 2 | no Item Type or Slot row (Rune Arm, gem style items) |

Consequences: `slot` and `item_type` exist on only the accessory template. For armor, weapons and shields the equipment slot must be derived from the template and the type row.

## Row label variants (canonical field: labels seen)

- minimum_level: `Minimum Level`
- required_race: `Race Absolutely Required` (`None` string is a value, not absence)
- excluded_race: `Race Absolutely Excluded` (unmapped today)
- material: `Material`, `Made from`
- weapon proficiency: `Proficiency Class`, `Proficiency`
- damage: `Damage and Type`, `Damage`; crit: `Critical threat range`, `Critical Roll`
- armor max dex: `Maximum Dexterity Bonus`, `Max Dex Bonus`
- effects list: `Enchantments`, `Enhancements`
- location: `Location`, `Locations`
- bind: `Binding` (one page also has `Bind Status`)
- Other rows with no field today: Feat Requirement, Required Trait, Attack Mod, Damage Mod, Use Magical Device DC, Accepts Sentience?, Upgradeable?, Notes, Tips, Shield Type, Shield Bonus, Damage Reduction, Rarity, Augment Type, Maximum Stack Size. Kinetic Sphere and one Rune-Arm style page also carry spell-like rows (Target, School, Spell Resistance, Charge Tier I-V).
- Armor Bonus can have per-variant sub-rows ("Adamantine Body: +26 / Mithral Body: +15 / Composite Plating: +6").

## Where the original normalizer failed

Measured on the 40 pages (fields populated / 40):

- **Weapon damage never parses (0/10).** Format is `[1d10] + 8 Slash, Magic`; the regex expects `1d8+5` with no brackets. Damage type is merged into the same cell (`Damage and Type`), so `damage_type` is empty everywhere.
- **`slot` 18/40, `item_type` 18/40** for the reason above.
- **`Enhancements` is unmapped**, so both shield/orb pages and the cosmetic armor page report zero effects.
- **Bonus type is discarded.** The visible text is `Corrosion +59` while the type ("Equipment bonus") lives only in the tooltip span the parser deletes. The tooltip text ADR 0008 wants is discarded too.
- **Values wrong or missing:** `+7 Enhancement Bonus` becomes a name with no value; `Ice Lore +21%` misses the suffix regex; `Holy Burst 4`, `Fracturing 6`, `Bloodletter VII` are ambiguous (tier vs magnitude).
- **The Enchantments list mixes six kinds of entries** with no classification:
  1. effect with value (`Corrosion +59`), bonus type in tooltip link (`/page/Equipment_bonus`)
  2. effect without value (`Antipodal`, `Cannith...`)
  3. set-bonus lines (`3 Pieces Equipped: +15% Artifact bonus to Doublestrike`) and a set-name effect (`Might of the Abishai`)
  4. augment slots (`Blue Augment Slot`, `Upgradeable - Primary Augment (...)`)
  5. Customisation hints (`Mythic Weapon Boost +2 or +4`, often "rare enchantment, not on all drops")
  6. item-unique systems with nested lists: `Nearly Finished` (one of the following), `Attuned by Heroism` (relic tiers), `Rune Arm Imbue`, `Maximum Charge Tier`
- **Source is unstructured:** `Location` yields quest, chest kind ("End chest", "raid chest", "End chest (Epic Elite)") and crafting recipes (`I:X + I:Y`) in one comma list.
- **Binding is free text with NBSP** ("Bound to Account\xa0on Acquire", "...on Equip") and needs mapping to the spec enum.
- **Cosmetic and "None" values:** the Frock Vest has `None` strings for level, material and value, and zero durability. `None` must mean null.
- **Multi-line cells:** rows like `Material` have a trailing empty cell; `Weight` and `Handedness` are often empty.

## Proposed design: config-driven extractor

Two stages, so scraping never depends on the effects registry (ADR 0008):

1. **Extract** (config-driven): HTML to a *scraped item* JSON, committed under `catalog-src/items/` (ADR 0006). Effects carry the raw name, parsed value, bonus type and tooltip. No registry lookups.
2. **Compile** (later slice): scraped items plus the registry and rules to the spec's bundle `items.json`.

Config lives in the data repo as YAML (`catalog/extractor/`), validated by JSON Schema:

- `fields.yaml`: canonical field, alias group (all label variants), coercer (`int`, `float`, `copper`, `percent`, `text`, `null_if_none`, `list`), and which templates it applies to.
- `templates.yaml`: template detection (first row label or row-set signature) and per-template category and slot derivation.
- `enchantments.yaml`: ordered classification patterns for list entries (effect+value, effect, set bonus, augment slot, customisation hint, unique system, tier) with named captures; the bonus type comes from the tooltip link pattern `/page/<Type>_bonus`.
- `mappings.yaml`: value maps (binding text to the spec enum, damage-type abbreviations, proficiency names).

Anything unmatched is reported (not dropped): unknown row labels, unclassified list entries and template misses go to a per-run report so new wiki variations become config edits, not code changes.

## Result of the first build

`src/item_extractor` implements stage 1 with config in `catalog/extractor/`. Run it against the sample cache:

    python -m item_extractor cache --out cache/extracted --report cache/report.json

or inspect one page with `ddoloot extract-item "<item name>"`.

On the 40 pages: all five templates detected (18 accessory, 10 armor, 8 weapon, 2 shield, 2 untyped), no extraction failures, no unmapped rows, no unclassified effects. Crit, slots, item types, page id and revision id are populated. **Weapon damage is not:** the wiki writes it with a weapon-dice multiplier (`5.20[1d8+2] + 15 Pierce, Magic`), which the `damage` coercer does not read. On the 7 cached weapon pages whose damage cell is written this way, `damage_dice`, `damage_bonus` and `damage_types` are null, and the raw cell text is recorded in the Scraped Item's `extraction_errors` under `weapon_stats.damage_dice`. Parsing it needs a field for the multiplier (follow-up). Other results: bonus types are captured for 139 of 186 effects (of the other 47, only two mention a bonus in their tooltip: the set-marker and one "Legendary Elemental Energy" case below). Things still needing a decision downstream (stage 2 / the effects registry): bonus-type strings such as `insightful` (redirect of Insight) and `dodge`, and set-marker effects like "Against the Slave Lords Set Bonus" that are not tied to a tooltip list. The original normalizer package has been deleted.
