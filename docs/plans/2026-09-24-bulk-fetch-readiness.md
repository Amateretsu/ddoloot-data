# Bulk fetch readiness

Prepare ddoloot-data for the bulk backlog fetch. Fix the pipeline issues and extractor
gaps recorded in `2026-09-24-catalog-src-backlog.md` ("Session summary", "Pipeline issues
found", "Gaps by kind").

Judgment calls come from `CONTEXT.md`, the ADRs in `ddoloot-app/docs/adr` (0003, 0006,
0007, 0008 and 0009 especially), and the plans in this directory. Where they say nothing,
the option with the smallest interface is chosen, and the choice is recorded below.

## Baseline

- Tests: 322 passed, 1 skipped.
- Report totals (pilot plus batch 1, 213 item report lines): 20 unmapped rows,
  60 unclassified effects, 5 extraction errors, 0 warnings.

## Rules

- No network to ddowiki.com, except the single optional probe in Step 7. Every re-run goes
  through `scripts/offline_rerun.py` against the held Page Store: `cache/pages` holds 202
  `Item:` pages and the update pages for Updates 5-13.
- ddoloot-app is read only. An existing UUID in `catalog-src/registry.jsonl` is never
  deleted or rewritten. The committed `config/scraper.yaml` politeness values stay as they
  are. The only runtime dependencies are pydantic, requests, beautifulsoup4, pyyaml,
  jsonschema, tenacity and loguru.
- ADR 0006 holds: no stealth, spoofing, proxies or CAPTCHA services, and no `/api.php`,
  `index.php` or `Special:` pages.
- A change to the Scraped Item schema is backward compatible and consistent with the
  ADRs. A decision that the ADRs reserve for the maintainer, or for the review gate
  (ADR 0008: new effects and bonus types), is not made here. It goes into a proposal
  section in this plan, and the gap stays in the baseline.
- Every fix is tested through the public interface: `extract()` for the extractor, the
  queue's own interface for ordering, and the Page Store with fake transports for fetch
  behaviour.
- After any extractor or writer change:
  - re-run offline over all held pages, Updates 5-13;
  - regenerate the gaps baseline:
    `UPDATE_GAPS_BASELINE=1 .venv/bin/pytest -q tests/item_extractor -k whole_page_store`;
  - review the item-file diff, which must show only the intended changes;
  - run the harness a second time and confirm that `git status` stays unchanged;
  - confirm that `check_catalog('catalog-src')` returns [].
- CI (ruff, black --check and isort --check on `src tests`, and pytest on 3.11 and 3.12)
  must be green.

## Shipping

Every step is a branch, pushed, with a PR opened with `gh`. Each waits for CI to go green
(`gh pr checks --watch`), is merged with `gh pr merge --merge --delete-branch`, and local
main is pulled before the next step. Commit messages name the step.

## Steps

### Step 0: this plan

### Step 1: queue order

The queue processes update pages in `page_name` string order (10, 11, …, 5, 6). Change
that to ascending update number, with non-numbered pages last. Test it offline with a
multi-page queue whose names sort differently as strings and as numbers.

### Step 2: browser adapter status

After a WAF challenge, `BrowserTransport.fetch` checks only the first `goto` response's
status. Change it to use the final document's status. A missing page is then not stored as
a success, and its item is marked failed with a clear reason. Test it with a fake
browser/page.

### Step 3: pages with no infobox

Decide how a non-equipment item page is handled, in line with CONTEXT.md's Named Item.
It must stay visible in report.jsonl and in the gaps baseline, and must not count as a
failure. A genuinely broken page must still fail.

### Step 4: extractor gaps, one PR each

- a) clicky charges and recharge;
- b) the wand row `No UMD check for:`;
- c) binding with no timing;
- d) Exclusive;
- e) alignment DR and `Exceptional Fortification (+10%)` (ADR 0008 review gate);
- f) wiki bug notes inside effect text;
- g) `item_type` null for `accessory_untyped`.

Each is either fixed, if the ADRs, CONTEXT.md or the existing schema and rules already
give the answer, or recorded as a proposal. The before and after report totals are
recorded here.

### Step 5: bulk-run safety

A small script under `scripts/` runs one sync with `--verbose` into a log. It stops the
sync on 3 consecutive 429/5xx, or when failures exceed 10% of processed items. An
optional `--dry` mode prints the worst case `1 + 2F + min(F, k)` for unheld pages. The
arithmetic and the stop logic are tested offline.

### Step 6: the WAF stop

Investigate, don't retry. Record the likely causes and propose a mitigation that complies
with ADR 0006.

### Step 7 (optional, live, at most 4 requests): one probe

`ddoloot sync --verbose --scraper-config <scratch> --page Update_8_named_items --limit 1
--max-retries 0`, worst case 1 + 2 + 1 = 4.

### Session summary

## Proposal: binding with no timing

- **Gap:** 5 held items give a Binding with no timing. `Bound to Character` (4): Phiarlan
  Veil Shield (round, angular) and Stormreach Marketplace Shield (round, angular).
  `Bound to Account` (1): Glorious Obscenity. Each is written with `binding` and
  `binding_raw` null, and the raw text kept in `extraction_errors.binding`.
- **The source does not say either.** The wiki's `Template:Bind` adds
  `Category:Binds on acquire` or `Category:Binds on equip` when it is given a timing. Of
  the 202 held `Item:` pages, 190 are in the first and 4 in the second. These 5 pages are
  in neither, only in `Binds to character` or `Binds to account`. The other 3 pages in
  neither are `Unbound` or an ingredient. So the page, not the extractor, lacks the timing.
- **What the docs say:** CONTEXT.md's Binding has three states: bound to a DDO Account,
  bound to a Character, or bound on equip ("undecided until first equipped"). The
  `spec/v1/item.schema.json` enum (app-owned) is `account`, `character`, `on_equip`,
  `unbound` or null. `mappings.yaml` maps only timed forms: on acquire to the named target,
  either on equip form to `on_equip`. The deepen-page-store plan decided that unknown
  binding text is an extraction error, with `binding` and `binding_raw` null and the raw
  text in `extraction_errors`. The backlog plan left this gap open because "acquire and
  equip cannot be told apart". No doc says what an untimed wiki value means.
- **Options:**
  - A) Map the untimed forms to their named target, `bound to character` → `character`
    and `bound to account` → `account` (two `mappings.yaml` lines; `binding_raw` keeps the
    untimed text, so the guess stays auditable). If an item really binds on equip, its
    Item Instances are recorded as bound when they may not be yet. CONTEXT.md already lets
    a user move a Character-bound instance to the Shared Bank, so the harm is small.
  - B) Map them to `on_equip`. This loses the known target (Character or Account), and
    `on_equip` would claim a timing the source does not give. Not recommended.
  - C) Add an "unknown timing" value to the enum, or a separate timing field. The enum is
    in the app-owned `spec/v1`, so ddoloot-app must change too. Too big for 5 items.
  - D) Keep the status quo: an extraction error, visible in `report.jsonl` and the gaps
    baseline, until someone checks the items in game or the wiki pages gain a timing.
- **Recommendation:** A, once the maintainer accepts treating untimed as on acquire.
  On acquire is the wiki's usual case (190 of 194 timed pages), the target is known, and
  it is the smallest change. Until then, D stands.
- **Decision needed:** the maintainer decides between A and D (or checks the 5 items in
  game). C needs a ddoloot-app change and an ADR-level decision about the Binding enum.
- **Decided by the maintainer (2026-09-24):** A. The wiki's untimed forms mean on acquire:
  `Bound to Character` → `character` and `Bound to Account` → `account`. See "Decision 4c"
  below.

## Proposal: alignment DR and Exceptional Fortification

- **Gap:** 7 unclassified Effects, stored by the `plain_effect` fallback with their whole
  text as the name and `value`, `value_kind` and `bonus_type` null.
  - Alignment DR (6): `DR 5/Evil` (Templar's Bastion, Templar's Docent), `DR 15/Evil`
    (their Epic versions), `DR 5/Good` (Infested Armor) and `DR 15/Good` (Epic Infested
    Armor). The entry is a `DR` link to `/page/Damage_Reduction` followed by ` 5/Evil`.
    Its tooltip reads `Damage Reduction 5/Evil : Reduces physical damage by 5, except from
    Evil attacks.`
  - `Exceptional Fortification (+10%)` (1, Sustaining Symbiont), linked to
    `/page/Fortification`. Its tooltip gives a `+10% chance` and ends: "This ability is
    considered an Insight bonus when determining stacking with other sources of
    fortification".
- **Why this is not a parse fix like step 4a's clicky:** the clicky rule removed a suffix
  and kept the spell's name as written, so the gate still saw a name from the source.
  Neither family here can be parsed without choosing something the source does not state:
  - **DR:** the value sits inside the name, between `DR` and the bypass. Every parse
    builds a name the page never shows (`DR/Evil`, `Damage Reduction/Evil`, or `DR` with
    the bypass in a new field). Whether the bypass is part of the Effect's identity
    (`DR/Evil` and `DR/Good` as two Effects) or a qualifier of one `Damage Reduction`
    Effect is an Effect identity decision. ADR 0008 reserves those for the review gate,
    and keys are immutable once shipped (ADR 0005). DR also has no Bonus Type, and the
    Effect has no field for a qualifier. `shield_stats.damage_reduction` holds a shield's
    own DR number and does not fit either.
  - **Exceptional Fortification:** the rule vocabulary cannot read `(+10%)` today, so a new
    parenthesised-value rule is needed. That part alone would be a parse fix. The Bonus
    Type is not. The existing rules find no Bonus Type here: there is no `*_bonus` link,
    and the tooltip has no `+N% <Type> bonus`. So the parse would give
    `Exceptional Fortification`, 10, `percent`, bonus type null. `exceptional` is already
    extracted for 6 Effects (`Exceptional Wisdom +1` and others), but each of those
    tooltips says `+N Exceptional bonus`. This one says the bonus stacks as **Insight**.
    Choosing `exceptional`, `insight` or null, and whether the Effect is
    `Exceptional Fortification` or `Fortification` with a Bonus Type (the other 4 held
    Fortification Effects are `Fortification` with `enhancement`), are Bonus Type and
    Effect identity decisions for the gate. `catalog/effects/` and
    `catalog/bonus-types.yaml` are still empty, so no registry entry answers this yet.
- **Options, DR:**
  - A) A `kind: effect` rule, `^DR\s+(?P<value>\d+)\s*/\s*(?P<bypass>.+)$`, that gives the
    name `DR/<bypass>` (for example `DR/Evil`), `value` 5, `value_kind: flat` and bonus type
    null. This needs a small rule feature to build a name from captures. The gate then
    decides `DR/Evil` and `DR/Good` once each, not once per value.
  - B) Name `Damage Reduction`, value 5, and a new optional Effect field `bypass`
    (`str | None`, default null). This is backward compatible for the Scraped Item. The
    bundle's app-owned `item_effect` has no such field, so the bypass would be lost at
    compile time unless ddoloot-app changes.
  - C) Keep the status quo: the raw names reach the gate as they are (`DR 5/Evil` and
    `DR 15/Evil` are separate raw strings), and the maintainer merges or ignores them.
    Each value is then its own alias, and the value is not machine-readable.
- **Options, Exceptional Fortification:**
  - D) A parenthesised-value rule, `^(?P<name>.+?)\s*\(\+(?P<value>\d+)(?P<pct>%)?\)$`,
    giving `Exceptional Fortification`, 10, `percent`. The bonus type stays null for the
    gate to decide, or a hand-written override in `catalog/rules/overrides` sets it.
  - E) The same rule, with the bonus type fixed to `insight` from the tooltip's stacking
    sentence. That is one page's wording and would need a new tooltip pattern. Not
    recommended for 1 item.
  - F) Keep the status quo, as in C.
- **Recommendation:** A for DR, because the bypass is what tells the Effects apart in play
  and the value becomes machine-readable without a schema change. D for Exceptional
  Fortification, with the Bonus Type decided at the gate. Until the maintainer decides,
  C and F stand: the 7 stay unclassified in `report.jsonl` and the gaps baseline.
- **Decision needed:** the maintainer, as the ADR 0008 reviewer, decides:
  - whether a DR bypass is part of the Effect's identity (A), a qualifier (B, which also
    needs a ddoloot-app change to reach the bundle), or neither (C);
  - whether `Exceptional Fortification` is its own Effect or `Fortification` with a Bonus
    Type, and which Bonus Type (`exceptional` as named, or `insight` as the tooltip says
    it stacks).
- **Decided by the maintainer (2026-09-24):** A for DR: the bypass is part of the
  Effect's identity, so `DR 5/Evil` is the Effect `DR/Evil` with value 5 (`flat`, no Bonus
  Type), and likewise for `DR 15/Evil`, `DR 5/Good` and `DR 15/Good`. For Exceptional
  Fortification: the Effect `Fortification`, 10, `percent`, Bonus Type `exceptional` as
  the item names it. See "Decision 4e" below.

## Proposal: item_type for accessory_untyped

- **Gap:** 24 held items use the `accessory_untyped` template (the `default: true`
  fallback in `templates.yaml`, reached when no weapon, armor or shield first label and no
  `Item Type` row matches). All 24 are written under category `other`, with `item_type`
  null and `equip_slots` []. They are two kinds, and the wiki categories split them exactly:
  - 20 wands, all in `Category:Named wands` (and `Eternal wands`): the 12 +N Eternal Wands
    of Disrupt Undead and Nimbus of Light, Roderic's Wand and its Epic version, Brimstone
    Verge and its Epic version, Cacophonic Verge, its Epic and Dampened versions, and Wand
    of Blasting. Each infobox starts with `UMD Difficulty`.
  - 4 rune arms, all in `Category:Rune Arms` (and `Craftable rune arms`): Animus,
    Chulchannad's Claw, The Pea Shooter and Glorious Obscenity. Each infobox starts with
    `Minimum Level` and has `Required Trait: Artificer Rune Arm Use`. The wiki also puts
    them in `Minimum level N weapons`.
- **No page states a type or slot.** None of the 24 infoboxes has an `Item Type`,
  `Weapon Type`, `Slot` or other type or equip row. Every held `item_type` value today
  (`Ring`, `Long Sword`, `Heavy Armor` and others) is text read from such a row, and every
  `equip_slots` value is mapped from a `Slot` row or fixed by a typed template. The only
  signals for these 24 are the wiki categories, and, for wands, the first row label.
- **What the docs say:** `spec/v1/item.schema.json` (app-owned) types `item_type` as a
  nullable free string with no enum. So null is allowed, and here it means "the page names
  no type". `category` is an enum, and `other` is valid for both kinds. CONTEXT.md and the
  ADRs do not define `item_type` or its vocabulary. No mapping or template rule reads a wiki
  category into a field. Step 3 reads categories only to skip ingredient pages, as an
  extractor constant.
- **Options:**
  - A) Two templates before the default. `wand` (`first_label: [umd difficulty]`) with a
    fixed `item_type: Wand`, and `rune_arm` (`has_label: [required trait]`, or a category
    check) with `item_type: Rune Arm`. Both keep `category: other` and `equip_slots: []`.
    This needs a new `item_type` key for a fixed value in the template config. `Wand` and
    `Rune Arm` are new values, chosen here rather than read from a row. The wand signal is
    a layout convention, and `Required Trait` is not unique to rune arms in general.
  - B) The same values, derived from the wiki categories (`Named wands` → `Wand`,
    `Rune Arms` → `Rune Arm`) by a new `item_type_from_category` map in `templates.yaml`.
    This is deterministic on every held page, but it is a new signal source for fields.
    The singular names are still chosen, and a future wand page missing from
    `Named wands` would stay null.
  - C) As A or B, and also set `equip_slots` (for example `off_hand` for rune arms). No page
    states a slot, so this is game knowledge. Not recommended here.
  - D) Keep the status quo: `other` with null `item_type`. The rows are correct as written,
    and the app can show "Other" with no subtype.
  - A separate question is whether rune arms belong under `weapon` rather than `other`,
    since the wiki lists them as `Minimum level N weapons`. Changing that moves 4 files in
    `catalog-src`, so it is not done here.
- **Recommendation:** B, once the maintainer accepts `Wand` and `Rune Arm` as `item_type`
  values. The wiki's own categories are the most direct statement of what each item is,
  the split is exact on all 24 held pages, and it keeps `category` (`other`) and every
  file path unchanged. No schema change is needed, because `item_type` is a free string.
  Until then, D stands.
- **Decision needed:** the maintainer decides:
  - whether `Wand` and `Rune Arm` become `item_type` values (B, or A);
  - whether wiki categories may feed Scraped Item fields;
  - whether rune arms stay `other` or become `weapon`.
  Equip slots for either kind (C) would need a game-knowledge source and are not proposed.

## Decisions made during execution

### Step 1: queue order

- **Where:** `QueueRepository` in `src/ddo_sync/queue_db.py`. Both orderings were by
  string: `list_update_pages()` (the order `sync_all` reads update pages and queues their
  links) by `page_name`, and `get_pending_items()` (the order `process_queue` writes
  items) by `queued_at`, which follows the string order in which pages were read.
- **Change:** both now order by update number parsed from `Update_<N>_named_items`
  (a deterministic SQL function registered on the connection), with non-numbered pages
  last, by name. Pending items within a page follow row id, which is document order.
  `limit` applies after ordering, so a partial batch takes the lowest updates first.
- **Decision:** `queued_at` is dropped from the pending order rather than kept as a
  tiebreak. The update number is the order that matters (an item lands under its
  introducing update first, so a multi-page batch causes no writer moves), and row id
  already gives FIFO within a page. No schema change; the interface is unchanged.
- **Tests:** two queue tests replace the by-name listing test, with pages Update_10,
  Update_9, Update_5 and two non-numbered pages: page listing order, and pending order
  with and without `limit`. Both fail on main.
- **Offline rerun, Updates 5-13:** 0 fetch attempts, no `catalog-src` changes,
  `check_catalog('catalog-src')` returns [].
- Tests after step 1: 323 passed, 1 skipped.

### Step 2: browser adapter status

- **Change:** `BrowserTransport` listens for `response` events and keeps the latest
  main-frame navigation response (the challenged document, then its reload). Once the
  article content appears, `fetch` returns that response's status instead of a fixed 200.
  The early 404 on the first `goto` response and the 202 "challenge not cleared" reply are
  unchanged.
- **Decision:** the adapter reports the status and the Page Store decides, as on the plain
  path, so no store or syncer code changed. A reload answering 404 raises
  `FetchError("page not found (404): <url>")`, is not retried or stored, and the syncer
  marks the queue item failed with `FetchError: page not found (404): <url>`, the same
  reason as a plain 404. Other statuses follow the plain rules too (429/5xx retried, other
  4xx fail). The `goto` response is used only when no navigation response was seen.
- **Tests:** a fake Playwright in `tests/canned.py` (`FakeBrowserPage`, installed as
  `playwright.sync_api`), so nothing launches and nothing reaches the wiki. Through the
  Page Store: challenge-then-404 is not stored and fails with status 404; challenge-then-200
  is stored via the browser. Directly on the adapter: the status is the final document's,
  including after a non-navigation response. Through the syncer: the item is failed with
  the 404 reason and nothing is written. Four of these fail on main.
- **Page Store:** `Update_4_named_items` (the "no article" page from backlog step 1) is
  still held in `cache/pages` as a stored success. Left in place, as instructed.
- **Offline rerun, Updates 5-13:** 0 fetch attempts, no `catalog-src` changes.
- Tests after step 2: 332 passed, 1 skipped.

### Step 3: pages with no infobox

- **Decision:** a crafting-ingredient page is not a Named Item. CONTEXT.md defines a Named
  Item as a uniquely named piece of DDO loot that players record Item Instances of, with
  Customisations, Effects and Binding. The 5 pages (Token of the Twelve, Mark and Legendary
  Mark of Rhesh Turakbar and of Sheshka) are stackable crafting materials with none of
  these. They are listed on update pages, but they are not in the catalog. So each is
  **skipped**:
  - the queue row is marked `complete` and `process_queue` counts it as a success, not a
    failure, so it does not count towards the bulk run's 10% stop;
  - its `report.jsonl` line is `{name, url, update_page, skipped: "<reason>",
    extraction_errors: {}, warnings: []}`;
  - no item file, no registry line and no UUID.
- **Signal:** the page has no infobox table **and** MediaWiki's `wgCategories` lists
  `Ingredients` (Token of the Twelve) or `Raw ingredients` (the 4 Marks). None of the 197
  other held `Item:` pages is in either category. The check runs only when there is no
  infobox. A page with no infobox and no ingredient category still raises
  `ExtractionError: no infobox table` and fails, and so does a page whose category list is
  missing or unreadable, or a page with no `mw-parser-output`. The two category names are
  a constant in the extractor, not config.
- **Interface:** one new outcome and one writer method.
  - `extract()` raises `NotEquipmentError`, a subclass of `ExtractionError`, so existing
    callers (the `extract` CLI command) keep working.
  - `ScrapedItemWriterProtocol` gains `skip(url, report)`: `CatalogWriter.skip` writes
    only the report line, and the in-memory fake records it.
  - The gaps baseline has a new kind, `skipped`, in place of `extraction_failed` for the
    5 pages.
- **Tests:**
  - Through `extract()`: the real Mark of Sheshka page is committed as a fixture and is
    classified as not equipment. The same page with an equipment-only, empty, missing or
    garbled category list still fails with `no infobox table`. An infobox page in
    `Raw ingredients` still extracts.
  - Through the syncer with `CatalogWriter`: the row is `complete` and counts as
    (1, 0), the report line is exact, and no item file or registry line is written.
- **Offline rerun, Updates 5-13:** 0 fetch attempts; the script exits 0, and the inner
  sync exit code is 2 (43 unheld pages fail as `NotHeld`, as before). Queue cycle: 210
  successes and 43 failures, up from 205 and 48. No `catalog-src` changes: `report.jsonl`
  lives in the gitignored `cache/extracted`. The 5 skip lines went to update-5 (Token)
  and update-8 (the Marks). A second run left `git status` unchanged, and
  `check_catalog('catalog-src')` returns [].
- **Unheld pages likely to be skipped too:** the pending Update 8 rows include
  `Mark_of_Bal_Molesh`, `Mark_of_Tzaryan_Rrac` and their Legendary versions. They are
  probably the same ingredient pages, and are not verified here.
- **Report totals** (all `cache/extracted/*/report.jsonl`):

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 205 | 20 | 60 | 5 | 0 | 0 |
  | After | 210 | 20 | 60 | 5 | 0 | 5 |

  The 5 extraction errors are 5 binding fields (`Bound to Character` 4,
  `Bound to Account` 1), not the 5 ingredient pages. Those pages had no report line
  before this step. This count finds 205 lines before this step, not the 213 that the
  baseline records. The other totals match.
- Tests after step 3: 339 passed, 1 skipped.

### Step 4a: clicky charges

- **Outcome: fixed.** The data gave the answer, with one backward-compatible schema
  addition.
- **What the docs say:**
  - CONTEXT.md defines an Effect as "a named game effect on a Named Item, carrying a
    tooltip explanation … with an optional value and Bonus Type", and says some Effects
    have no value. A Scraped Item "carries every Effect with its raw name".
  - The extractor already stored each clicky as an Effect: the `plain_effect` fallback
    kept the whole text (`Haste — 3 Charges`) as its name and flagged it as unclassified.
  - No ADR reserves a Scraped Item field. ADR 0008 reserves new effects and bonus types
    for the review gate at catalog build. Parsing a clicky makes no gate decision: the
    gate still decides each raw name. It now sees the spell (`Haste`) instead of a
    per-item string such as `Haste — 3 Charges`. No new rule kind and no new Bonus Type
    were added.
  - The bundle's `item_effect` (`spec/v1`, owned by ddoloot-app) has only `effect`,
    `bonus_type` and `value`. The Scraped Item lives in the data repo and is not part of
    that spec, so this change needs nothing from ddoloot-app.
- **Decision:**
  - A clicky is an Effect named after its spell, with `value`, `value_kind` and
    `bonus_type` null. `Effect` gains two optional fields, `charges` and
    `recharge_per_day`, both `int | None` and defaulting to null. Every other Effect
    writes them as null. Two scalars were chosen over a nested `clicky` object, because
    that is the smaller interface.
  - Charges are not put in `value`. `value` is the Effect's magnitude alongside a Bonus
    Type, and a charge count would be read as one.
  - Raw text: an Effect has no raw field. Like every other rule, the name holds the raw
    name (the spell, as written), and the tooltip is kept. The parts removed from the
    name are exactly the two numbers now held in `charges` and `recharge_per_day`.
- **Rule:** in `enchantments.yaml`, `clicky` (`kind: effect`) sits before `bonus_to`:
  `^(?P<name>.+?)\s+[—-]\s+(?P<charges>\d+) Charges?(?:\s*\(Recharged/Day:\s*(?P<recharge>\d+)\))?$`.
  - It needs a spaced em dash or hyphen, a number and `Charge(s)` at the end, and it
    optionally takes `(Recharged/Day:N)` with any spacing.
  - Every form in the held data: em dash with or without recharge (48), and hyphen
    with `Recharged/Day: N` (4). The wiki's double space collapses before matching.
  - `_effect()` fills `charges` and `recharge_per_day` from the `charges` and `recharge`
    captures, so the code has no clicky-specific branch.
  - Over-match check: over all 202 held `Item:` pages, the rule hits exactly the 52
    clicky entries. No other Effect, including `Maximum Charge Tier` and the
    `Charged Gauntlets` names, changed.
- **Not covered:** an Eternal Wand's infobox header also shows its spell, caster level,
  `50/50 Charges` and `Recharged/Day: 50`. That is outside the Effects cell. It
  duplicates the Enchantments entry, so it is left alone. Getting charges and recharge
  into the bundle needs a place in the app-owned `item_effect` spec. That is for the
  compile stage and ddoloot-app later, and is not needed to hold the data here.
- **Files:** `src/item_extractor/scraped_item.py`, `src/item_extractor/effects.py`,
  `catalog/extractor/enchantments.yaml`, `tests/item_extractor/test_extract.py`,
  `tests/item_extractor/extractor_gaps.json`, and 197 item files under `catalog-src/items`.
- **Tests (through `extract()`):**
  - four clicky forms: em dash with and without recharge, hyphen with `&nbsp;` spacing
    and a tooltip, and a name that contains parentheses;
  - three near-misses that are not clickies.
  All 7 fail on main.
- **`catalog-src` diff:** 197 item files, and no registry change. It was verified
  programmatically:
  - every Effect gains `"charges": null, "recharge_per_day": null`, except the 52
    clickies (26 with a recharge);
  - each clicky's name loses only its charge suffix;
  - nothing else in any file changed.
  The gaps baseline loses the 52 clicky entries: 52 pages changed, 70 → 38 entries.
- **Offline rerun, Updates 5-13:** 0 fetch attempts, 43 unheld pages skipped, and the
  inner sync exit code is 2. A second run left `git status` and the diff unchanged, and
  `check_catalog('catalog-src')` returns [].
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 210 | 20 | 60 | 5 | 0 | 5 |
  | After | 210 | 20 | 8 | 5 | 0 | 5 |

  The 8 left are 6 alignment DR entries, `Exceptional Fortification (+10%)` and the bug
  note (steps 4e and 4f).
- Tests after step 4a: 346 passed, 1 skipped.

### Step 4b: wand UMD row

- **Outcome: fixed**, with one backward-compatible schema addition.
- **What the row holds:** in all 20 held wand pages, `No UMD check for:` sits beside
  `UMD Difficulty` and lists abbreviated classes that use the wand with no Use Magic
  Device check: `Wiz, Sor` (8), `Clr, FvS` (6), `Wiz, Sor, Brd` (3),
  `Wiz, Sor, Clr, FvS, Brd` (2) and `Wiz, Sor, DDM` (1). No other field captures it:
  `umd_dc` holds the DC, and `required_class` is a different row. So it does not
  restate anything, and an ignore entry would drop data. The pilot (catalog-src-item-files
  plan, step 4) refused to ignore it for the same reason.
- **What the docs say:**
  - CONTEXT.md: a Scraped Item is the record of a Named Item "as read from its wiki page".
    No ADR reserves Scraped Item fields. ADR 0008 covers only Effects and Bonus Types, and
    this row is neither.
  - `fields.yaml` already maps each requirement-like row, such as `required_class`,
    `required_race` and `umd_dc`, to a raw-text `str | None` field with the `text`
    coercer. The Scraped Item is not in the app-owned `spec/v1` bundle schema, so
    ddoloot-app needs no change. Step 4a set the precedent: an optional field that
    defaults to null.
- **Decision:**
  - `ScrapedItem` gains `umd_exempt_classes: str | None = None`, next to `umd_dc`.
  - `fields.yaml` maps the label `no umd check for` to it with `coerce: text`.
  - The value is the raw text as written (`Wiz, Sor`). It is not split into a list, and
    the abbreviations are not expanded into class names. That matches `required_class`,
    needs no new coercer or class map, and is the smallest interface. Expanding the
    abbreviations is left to the compile stage if the bundle ever needs it.
- **Files:** `src/item_extractor/scraped_item.py`, `catalog/extractor/fields.yaml`,
  `tests/item_extractor/test_extract.py`, `tests/item_extractor/extractor_gaps.json`, and
  197 item files under `catalog-src/items`.
- **Tests (through `extract()`):** two new `test_row_is_coerced_into_its_field` cases. One
  is the plain label, and the other has a linked `UMD` in the label and a five-class
  value. Both fail on main.
- **`catalog-src` diff:** 197 item files, 1 added line each, and no registry change. It was
  checked programmatically: the 20 wands gain their raw value, and the other 177 files
  gain `"umd_exempt_classes": null`. The gaps baseline loses the 20 wand entries (100
  lines), because they had no other gap.
- **Offline rerun, Updates 5-13:** 0 fetch attempts, 43 unheld pages skipped, and the
  inner sync exit code is 2. A second run left `git status` and the diff unchanged, and
  `check_catalog('catalog-src')` returns [].
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 210 | 20 | 8 | 5 | 0 | 5 |
  | After | 210 | 0 | 8 | 5 | 0 | 5 |

- Tests after step 4b: 348 passed, 1 skipped.

### Step 4c: binding with no timing

- **Outcome: proposed**, not fixed. See "Proposal: binding with no timing" above.
- **What the docs say:** CONTEXT.md (Binding), the `spec/v1` binding enum, `mappings.yaml`
  and the earlier plans name only the timed forms. None says what the wiki's untimed
  `Bound to Character` or `Bound to Account` means, and no ADR covers Binding. The held
  pages confirm the wiki gives no timing: none of the 5 is in `Binds on acquire` or
  `Binds on equip`.
- **Decision:** the 5 stay unmapped. No enum value was added and nothing was mapped to
  acquire. The current record is correct and kept. As the deepen-page-store plan decided,
  `binding` and `binding_raw` are both null, and the raw text is in
  `extraction_errors.binding`; the report line reads
  `"extraction_errors": {"binding": "Bound to Character"}`. That names the field and the
  unmapped text, so the report entry is clear as it is. (The step's task said
  `binding_raw` keeps the text; it does not. The raw text lives only in
  `extraction_errors`.)
- **Files:** this plan only. No extractor, mapping, schema or writer change, so no
  offline re-run and no baseline regeneration. `catalog-src` diff: none.
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 210 | 0 | 8 | 5 | 0 | 5 |
  | After | 210 | 0 | 8 | 5 | 0 | 5 |

- Tests after step 4c: 348 passed, 1 skipped (unchanged).

### Step 4d: Exclusive

- **Outcome: fixed**, with one backward-compatible schema addition.
- **What the source holds:** 49 of the 197 held equipment pages end their Binding row in
  `, Exclusive` (`Bound to Account on Acquire , Exclusive` 34,
  `Bound to Character on Acquire , Exclusive` 15). The wiki links it to its `Exclusive`
  page, and the same 49 pages, and no others, are in `Category:Exclusive`. No other row
  expresses it. The only other mention is a Tips note on Shard of Xoriat ("Early version
  of this item was not exclusive … you cannot loot a new one"), which is prose and stays
  in `tips`. Every held equipment page has a Binding row, so none is null today.
- **What the docs say:** CONTEXT.md, the ADRs and `spec/v1` do not mention Exclusive
  (`spec/v1`'s only match is the unrelated `mutually_exclusive` constraint). The pilot and
  backlog plans recorded "there is no Exclusive field" as a gap needing a schema decision,
  with the flag kept only in `binding_raw`. No ADR reserves Scraped Item fields, and
  Exclusive is neither an Effect nor a Bonus Type, so ADR 0008 does not apply. Steps 4a
  and 4b set the precedent of an optional Scraped Item field defaulting to null. The
  Scraped Item is not in the app-owned bundle spec, so ddoloot-app needs no change.
- **Decision:**
  - `ScrapedItem` gains `exclusive: bool | None = None`, next to `binding_raw`.
  - It is read only from the Binding row, the field's own source: `true` when the row
    ends in `, Exclusive` (any spacing and case), `false` when a Binding row exists
    without it (including `Unbound`), and null when there is no Binding row or it says
    `None`. `Category:Exclusive` agrees on every held page, so a second source was not
    added.
  - The binding coercer strips the suffix before the `mappings.yaml` lookup. The two
    `, exclusive` mapping keys are removed, since they would never match again.
    `binding_raw` still holds the full text, so it is unchanged.
  - For an unmapped binding (step 4c's untimed forms), `exclusive` is still set from the
    row. `Unparseable` gains an optional `partial` dict, which the extractor sets for a
    spread row after recording the error. The binding stays null with its raw text in
    `extraction_errors.binding`, as before. The 5 held untimed items have no suffix, so
    they are `false`.
- **Files:** `src/item_extractor/scraped_item.py`, `src/item_extractor/coercers.py`,
  `src/item_extractor/extractor.py`, `catalog/extractor/mappings.yaml`,
  `tests/item_extractor/test_extract.py`, and 197 item files under `catalog-src/items`.
- **Tests (through `extract()`):** `test_exclusive_is_read_from_the_binding_row`, 8 cases:
  account and character Exclusive, a plain binding under `Bind Status`, `Unbound`, an
  untimed binding with and without Exclusive (the error is still recorded), no Binding
  row, and a `None` row. All 8 fail on main.
- **`catalog-src` diff:** 197 item files, 1 added line each, and no registry change. It
  was checked programmatically: each file's only change is the new key, 49 `true` and
  148 `false` (the 5 untimed items among them), each matching the binding text's suffix.
  The gaps baseline was regenerated and did not change.
- **Offline rerun, Updates 5-13:** the script exits 0 with 0 fetch attempts, 43 unheld
  pages skipped, and the inner sync exit code is 2. A second run left `git status` and the
  diff unchanged, and `check_catalog('catalog-src')` returns [].
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 210 | 0 | 8 | 5 | 0 | 5 |
  | After | 210 | 0 | 8 | 5 | 0 | 5 |

- Tests after step 4d: 356 passed, 1 skipped.

### Step 4e: alignment DR and Exceptional Fortification

- **Outcome: proposed**, not fixed. See "Proposal: alignment DR and Exceptional
  Fortification" above.
- **What the docs say:** ADR 0008 gives the review gate, at catalog build, every new
  Effect and Bonus Type, and ADR 0005 makes keys immutable once shipped. A scraper-side
  parse that only removes a value from a name as written (step 4a) makes no gate decision.
  `catalog/effects/` and `catalog/bonus-types.yaml` are still empty, so no registry entry
  settles either family. CONTEXT.md's Effect has an optional value and Bonus Type, and no
  qualifier.
- **Decision:**
  - DR: the value sits inside the name (`DR 5/Evil`). Any parse must build a name the page
    does not show and decide whether the bypass (`Evil`, `Good`) is part of the Effect's
    identity. That is an Effect identity decision, so it is proposed.
  - Exceptional Fortification: only the `(+10%)` form is new to the rules, but the Bonus
    Type is ambiguous. The name says Exceptional, the tooltip says it stacks as Insight,
    and the existing rules would find none. The other held Fortification Effects are
    named `Fortification` with `enhancement`. Choosing the Bonus Type and the Effect name
    is for the gate, so it is proposed too. `exceptional` is already extracted for 6
    Effects whose tooltips say `Exceptional bonus`, so it is not a new Bonus Type. The
    open question is which Bonus Type this Effect carries.
  - All 7 stay unclassified, visible in `report.jsonl` and the gaps baseline.
- **Files:** this plan only. No extractor, mapping, schema or writer change, so no
  offline re-run and no baseline regeneration. `catalog-src` diff: none.
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 210 | 0 | 8 | 5 | 0 | 5 |
  | After | 210 | 0 | 8 | 5 | 0 | 5 |

- Tests after step 4e: 356 passed, 1 skipped (unchanged).

### Step 4f: wiki bug notes

- **Outcome: fixed**, with one backward-compatible schema addition.
- **What the source holds:** 1 held occurrence, on Epic Golden Guile. The Enchantments
  entry is the `Improved Deception +17` link and its tooltip, followed by plain text with a
  bold label: ` (<b>Bug: </b> Provides +5 to bluff, not +17)`. There is no template class
  or span around it. After the tooltip is removed and whitespace collapsed, the entry reads
  `Improved Deception +17 ( Bug: Provides +5 to bluff, not +17)`, which no value rule
  matched, so `plain_effect` stored the whole text as the name and flagged it. The other
  `Bug:` texts in the held pages are in Tips rows (for example Twisted Talisman) and stay in
  `tips`.
- **What the docs say:** CONTEXT.md: a Scraped Item "carries every Effect with its raw
  name, value and Bonus Type". The bug note is neither: it is a wiki editor's comment on
  the entry. Stripping it makes no ADR 0008 decision: the name left is the one written on
  the page (`Improved Deception`, the same name the non-epic Golden Guile already gives),
  and the value and Bonus Type come from the existing rules. No ADR reserves Scraped Item
  fields, and the Scraped Item is not in the app-owned `spec/v1`. Steps 4a, 4b and 4d set
  the precedent of an optional field that defaults to null.
- **Decision:**
  - `effects.py` takes a trailing note off the entry text before the rules run. The
    pattern is narrow: `\s+\(\s*(?P<note>Bug:[^()]*?)\s*\)$`. It needs a space, a
    parenthesis, `Bug:` with a capital B, no nested parentheses, and the end of the entry.
    It is a module constant, like step 3's ingredient categories, not a rule key: it runs
    before routing, whatever rule later matches.
  - The note is kept on the Effect in a new `note: str | None = None`, as written
    (`Bug: Provides +5 to bluff, not +17`). Other places were considered and rejected:
    - `tooltip` is the wiki's hover text, and adding the note would change its meaning;
    - the item's `notes` and `tips` are their own infobox rows;
    - a report entry only would lose the note from `catalog-src`, because `report.jsonl`
      is gitignored, and the note says the stated value is wrong, which the maintainer
      should see next to the Effect.
    One scalar on the Effect is the smallest interface that keeps it.
  - A note on an entry that is not an Effect (a hint or a set row) has no field, so it is
    reported as a run-report warning rather than dropped silently. No held entry does this.
  - The Effect parses as `Improved Deception`, 17, `flat`, bonus type null. The tooltip
    says `+17 enhancement bonus` in lower case, which `tooltip_pattern` does not read. That
    is unchanged behaviour, not new to this step.
- **Files:** `src/item_extractor/scraped_item.py`, `src/item_extractor/effects.py`,
  `catalog/extractor/enchantments.yaml` (header comment only),
  `tests/item_extractor/test_extract.py`, `tests/item_extractor/extractor_gaps.json`, and
  197 item files under `catalog-src/items`.
- **Tests (through `extract()`):**
  - `test_bug_note_is_kept_apart_from_the_effect`: the real markup, and a no-value entry
    with `&nbsp;`;
  - `test_entry_without_a_trailing_bug_note_keeps_its_text`: 3 near-misses (text after
    the note, lower-case `bug:`, `Note:`), whose name stays the whole text;
  - `test_bug_note_on_a_hint_is_reported_not_dropped`.
  All 6 fail on main (the near-misses only because `note` did not exist).
- **`catalog-src` diff:** 197 item files, no registry change. It was checked
  programmatically: every Effect gains `"note": null` and nothing else changes, except
  Epic Golden Guile's entry. That one goes from `Improved Deception +17 ( Bug: … )` with
  null value to `Improved Deception`, 17, `flat`, with the note. The gaps baseline loses
  that one entry.
- **Offline rerun, Updates 5-13:** the script exits 0 with 0 fetch attempts, 43 unheld
  pages skipped, and the inner sync exit code is 2. A second run left `git status` and the
  diff unchanged, and `check_catalog('catalog-src')` returns [].
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 210 | 0 | 8 | 5 | 0 | 5 |
  | After | 210 | 0 | 7 | 5 | 0 | 5 |

  The 7 left are step 4e's alignment DR entries and `Exceptional Fortification (+10%)`.
- Tests after step 4f: 362 passed, 1 skipped.

### Step 4g: accessory_untyped item_type

- **Outcome: proposed**, not fixed. See "Proposal: item_type for accessory_untyped" above.
- **Affected:** 24 held items, all under `catalog-src/items/*/other/`: 20 wands
  (`Category:Named wands`) and 4 rune arms (`Category:Rune Arms`). The two categories
  split the 24 exactly and hold no other held page.
- **What the docs say:** `spec/v1/item.schema.json` types `item_type` as a nullable free
  string with no enum, so null is valid. CONTEXT.md and the ADRs do not define it.
  `templates.yaml` fills it only from a type row (`item_type_from` or
  `item_type_from_split`), and `mappings.yaml` has no item-type or category mapping.
- **Decision:** none of the 24 infoboxes has a type or slot row, so no existing rule or
  mapping gives a value. `Wand` and `Rune Arm` would be new values, taken from wiki
  categories or a layout convention, which no field reads today. That is a vocabulary
  decision for the maintainer, so it is proposed. The current record stays: category
  `other` (correct and unchanged, so no file moves), `item_type` null, `equip_slots` [].
  Null `item_type` is not a report gap (not an error or unmapped row), so the baseline
  does not list it.
- **Files:** this plan only. No extractor, mapping, schema or writer change, so no
  offline re-run and no baseline regeneration. `catalog-src` diff: none.
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 210 | 0 | 7 | 5 | 0 | 5 |
  | After | 210 | 0 | 7 | 5 | 0 | 5 |

- Tests after step 4g: 362 passed, 1 skipped (unchanged).

### Step 5: bulk-run safety

- **Committed:** `scripts/guarded_sync.py` and `tests/test_guarded_sync.py`. The script
  replaces the scratch watchdog from batch 1 (found in an old scratchpad and reused: the
  same 429/5xx warning pattern and the same `Failed: '` count). Every later live batch
  needs both the stop guard and the worst case written down first, so it earns its place,
  like `scripts/offline_rerun.py`.
- **Where the logic lives:** in the script only. The tests load it with `importlib`. The
  stop rules and the formula are operator tooling, not part of `ddo_sync`'s interface, so
  nothing was added to `src`. That is the smallest option. CI runs the tests but does
  not lint `scripts/`, so ruff, black and isort were run on the script by hand.
- **Guarded run:** `.venv/bin/python scripts/guarded_sync.py --log PATH <sync args>` runs
  one `ddoloot sync --verbose <sync args>` in its own session and writes all of its output
  to the log. It reads each line as it is written and sends SIGINT (like Ctrl-C, so the
  queue keeps its progress) when a stop rule holds. If the sync is still running 60 s
  later, its process group gets SIGTERM, then SIGKILL 10 s after that. Ctrl-C on the
  script is passed on as one SIGINT.
- **Log lines, read from the source** (the loguru format is
  `<time> | <LEVEL> | <message>`, coloured even into a file, so colours are removed first):
  - wiki request: `GET <url> -> <status> (plain)` (`HttpTransport`, redirect hops
    included), `GET <url> -> no response (plain)`, and `GET <url> (browser)`
    (`BrowserTransport._route`, no status). `blocked ...` lines are not requests.
  - 429/5xx: the Page Store's `<url>: HTTP <status> (attempt i/n)` warning. It is the one
    line that gives the status for both adapters, retries included.
  - items (`DDOSyncer.process_queue`): `Completed: '<name>'` (DEBUG), `Failed: '<name>' —
    <error>`, `Skipped: '<name>' — <reason>` (always followed by its `Completed:` line),
    and `Item '<name>' saved but mark_complete failed: ...` (a failure with no `Failed:`
    line).
- **Stop rules:**
  - 3 consecutive 429/5xx: each Page Store warning adds 1. A plain `GET` with any other
    status, or `Fetched '<title>'` (a page stored), resets the count. A browser `GET` line
    has no status, so it neither adds nor resets. So a browser 404 does not reset the
    count, which can only stop a run sooner.
  - failure rate: processed is `Completed:` plus failed lines, and the run stops when
    `failed >= 3 and failed * 10 > processed`. The floor of 3 was chosen over a minimum
    sample (such as "after 20 processed"): 1 of 2 and 2 of 2 do not stop, 3 failures in a
    row stop at once rather than after 20 items, and on a long run the rule is exactly
    "over 10%". Batch 1's ">22 of about 218" is the same rule at the end of that run.
    Skipped pages count as processed, not failed. An update page that cannot be read is
    not an item and is not counted.
- **Exit code:** the sync's own (0, 1 or 2), or 3 when the guard stopped it. Usage errors
  exit 64. The last line printed gives the reason and the tally: items processed and
  failed, and wiki `GET` lines in the log (the budget's request count).
- **Dry run:** `--dry <sync args>`, with `--page` required. It sends no request. The Page
  Store is built with transports that raise and a no-op sleep, and it is asked only
  `get(url)` (the public way to learn whether a page is held: a held page returns with no
  request, and an unheld one reaches a transport, which raises). The queue is read from a
  temporary copy, made with SQLite's backup from a read-only (`mode=ro`) connection. Then
  the queue's own interface runs on the copy, as the sync would:
  `register_update_page` for each `--page`, `reset_failed_to_pending(--max-retries)`,
  `list_update_pages()` and `get_pending_items(--limit)`. It prints
  `1 + 2F + min(F, k)`, where:
  - 1 is robots.txt;
  - F is the unheld pages the sync could fetch: every update page the sync reads (every
    page already in the queue, plus `--page`) that is not held, plus the unheld rows among
    the pending rows it would process, in its order and capped by `--limit`, after its own
    reset of failed rows;
  - 2F is one browser load per page, which may send 2 wiki requests (the document and its
    reload);
  - k is `browser.consecutive_challenges` from the scraper config: the challenged plain
    fetches in a row that switch the rest of the run to the browser. Before the switch,
    each challenged page also pays its plain fetch. The bound is exact for k = 1, and the
    script warns when k > 1. It also warns, with the `1 + max_retries` multiple, when the
    config's `max_retries` is not 0.
  - An update page that is unheld, or held but not yet read into the queue, adds rows that
    cannot be counted offline. The dry run then prints `F >= …` as a lower bound, or, with
    `--limit`, the upper bound `F <= unheld pages + limit`. `--refresh` counts every page
    as unheld.
- **The dry run cannot fetch, checked in the code:** the Page Store holds only the raising
  transports (so no `HttpTransport` or `BrowserTransport` is made), the dry path never
  starts a subprocess, and nothing else imported opens a connection. The sanity runs were
  also made with `socket.getaddrinfo` and `socket.connect` patched to raise. `md5` of
  `data/queue.db` and `cache/pages/index.json` was unchanged afterwards.
- **Sanity runs** against the persistent `data/queue.db` and `cache/pages`, with a scratch
  config (the committed values plus `max_retries: 0`, `browser.consecutive_challenges: 1`
  and `cache_dir` set to the absolute path of the repo's `cache/pages`):
  - `--page Update_8_named_items Update_9_named_items --limit 60 --max-retries 0`:
    ```
    update pages read: 9, unheld: 0
    pending rows processed (--limit 60): 48, unheld: 43
    k = browser.consecutive_challenges = 1
    F = 43; worst case 1 + 2F + min(F, k) = 88 requests
    ```
    This matches the backlog plan: 48 pending, 43 unheld, worst case 88.
  - `--page Update_14_named_items`:
    ```
    update pages read: 10, unheld: 1 (Update_14_named_items)
    pending rows processed (--limit none): 53, unheld: 43
    not yet read into the queue: Update_14_named_items; their item rows are not known offline.
    k = browser.consecutive_challenges = 1
    F >= 44; worst case 1 + 2F + min(F, k) >= 90 requests
    ```
    Two findings. First, the sync reads every update page in the queue and processes the
    whole pending queue, not only `--page`'s rows, so this run would also finish the 43
    unheld Update 8-9 rows. Second, without `--max-retries 0` the sync's default of 3 resets
    batch 1's 5 failed ingredient rows (53 = 48 + 5). They are held, so they cost nothing
    and are now skipped, but live runs should keep `--max-retries 0`.
- **Tests (offline, `tests/test_guarded_sync.py`, 33):**
  - the worst-case arithmetic;
  - each stop rule, on synthetic lines and on coloured lines copied from batch 1's log;
  - the 429/5xx rule on the Page Store's real warnings, captured from a `PageStore` whose
    canned transport answers 429, 502 and 503;
  - the dry run on a temporary queue and Page Store: queue order and `--limit`, failed-row
    reset, an unread page (both bounds), a missing queue DB, the k and `max_retries`
    warnings, and an unchanged queue file;
  - the subprocess path with a fake `python -c` child: a throttled child stopped by
    SIGINT, a child that ignores SIGINT and is terminated after the grace period, and an
    unstopped child whose exit code and log are kept.
- Tests after step 5: 395 passed, 1 skipped.

### Step 6: the WAF stop

- **Log found:** batch 1's `--verbose` log survived in the old session's scratchpad
  (`/private/tmp/claude-501/…/de24ce61-…/scratchpad/batch1.log`, 2,394 lines, with the
  scratch config and watchdog). No request was sent in this step.
- **What the log shows:**
  - Run start 09:27:59; robots.txt; `Update_10_named_items -> 202 (plain)` at 09:28:03,
    which switched the run to the browser (k = 1).
  - The browser session began at 09:28:08. The **only** challenge that cleared is the first
    one: `GET …/Update_10_named_items (browser)` at 09:28:08 and again (the reload) at
    09:28:09. There is no other repeated GET in the log, so **no challenge happened
    between the first one and the failure**. The run held one WAF token for 197 pages.
  - Pace: 199 browser GETs. The gap between the GETs of consecutive pages is 4 s in 195 of
    197 cases (one 2 s and one 5 s, second-level rounding). That is exactly the configured
    `crawl_delay_seconds: 4`. 15 requests a minute, flat from 09:28 to 09:41, with at most
    17 in any 60 s and 76 in any 300 s. Nothing sped up before the failure.
  - The failure, quoted in full, with nothing between the two lines:
    ```
    09:41:16 | DEBUG | GET https://ddowiki.com/page/Item:Crimson_Chain (browser)
    09:41:47 | DEBUG | queue_db closed: …
    09:41:47 | ERROR | Run stopped: WAF challenge on …/Item:Crimson_Chain not cleared in the browser
    ```
    One wiki document, no reload, and not one `blocked …` line (every earlier article load
    logs its blocked `load.php` and images a second after its GET). So the document that
    came back was not the article, and no second wiki document was ever requested. The
    31 s is the `goto` plus the 30 s `wait_for_selector`. The previous page
    (`Legendary_Mark_of_Sheshka`, 09:41:12) loaded normally.
  - Token age at the failure: the token came from the challenge cleared at 09:28:08-09, so
    it was 787 s (13 min 7 s) old; 783 s old on the last success.
- **How the adapter handles this** (`src/page_store/browser.py`, config `timeout_seconds:
  30`): `goto` waits for `domcontentloaded`. A 404 returns at once. Otherwise it waits up to
  30 s for `#mw-content-text`; if it never appears, the adapter returns a `202` with
  `x-amzn-waf-action: challenge`, and the Page Store stops the run. So **any** non-404
  document without an article (a 202 challenge, a 405 CAPTCHA, a 403 block, a slow error
  page) is reported as "challenge not cleared". The status of that document and its
  `x-amzn-waf-action` were not logged, nor were requests to other hosts (the AWS WAF
  challenge script and token service), so the log cannot say which it was. `_route` lets
  2 wiki `/page/` documents through per fetch and already logs any further one as
  `blocked document <url> (browser)` at DEBUG.
- **Likely causes, ranked:**
  1. **The WAF escalated after sustained volume** (a rate-based rule, or a Bot Control
     style per-session volume rule) to an action the adapter cannot pass: a CAPTCHA, a
     block, or a challenge whose token the service refused. Evidence for: one browser
     session sent 199 requests at a steady 15 a minute before it failed, and the failed
     load never reloaded, which a solvable challenge does within about 1 s (09:28:08 →
     09:28:09). Against a plain IP rate rule: the rate was flat for 13 minutes, so a
     limit over a 1-5 minute window would have tripped by about 09:33. Only a 10-minute
     window (about 150 requests, reached at about 09:38, plus AWS's evaluation lag) or
     a cumulative per-session or per-token count fits the timing.
  2. **Token lifetime.** It is unlikely to be the default 300 s: that would have produced a
     challenge (a reload in the log) at about 09:33, and none appears. An immunity time
     between 783 s and 787 s is possible but is not a round value. And an expired token
     normally gets an ordinary challenge, which this browser cleared in 1 s at the start;
     here there was no reload at all. Expiry alone does not explain the failure. Expiry
     plus a refused re-solve (cause 1) could.
  3. **The 2-document cap.** Ruled out for this failure. The cap logs every document it
     blocks, and none was logged; only 1 of the 2 allowed wiki documents was used. A
     second challenge step on another host (for example a CAPTCHA frame) is not capped
     at all.
  - Also possible, and not distinguishable from the log: a one-off failure of the
    challenge script or token service, or of the page itself.
- **What would tell them apart next time:**
  - The failing document's status and WAF action: 202 `challenge` (causes 1-2), 405
    `captcha` or 403 (cause 1).
  - Whether the challenge script and token calls ran.
  - The browser's age at the failure: a failure at about the same age regardless of pace
    points to the token (2), and one at about the same request count or rate points to
    volume (1).
  - Whether the step 7 probe (a fresh browser, hours later) clears at once: if it does, the
    block was temporary, not a standing ban.
- **Code change (diagnostics only, no behaviour change):** `BrowserTransport` now logs:
  - each main-frame document at DEBUG, as `document <url> -> <status>[, x-amzn-waf-action:
    <action>] (browser)`;
  - each request let through to another host at DEBUG, as `pass <type>
    <scheme://host/path> (browser)`, with the query dropped so no token is logged;
  - one WARNING when the article never appears, as `no article for <url> after 30s:
    N document(s) seen, M of 2 wiki requests used, last document <status>[, waf action];
    browser up <s>s (browser)`.
  None of these lines starts with `GET `, so `grep -c 'GET '` and
  `scripts/guarded_sync.py`'s request tally are unchanged. The fake Playwright's responses
  now carry `url` and `headers`, and a 202 document carries
  `x-amzn-waf-action: challenge`. Tests: `test_a_load_with_no_article_logs_the_last_document`
  (202, 405, 403), `test_a_cleared_challenge_logs_both_documents_and_no_summary`, and
  `test_routing_logs_passed_hosts_and_the_blocked_extra_document`. All 5 fail on main.
- **Mitigation (ADR 0006: same identity and pace, no proxy, stealth or CAPTCHA service;
  back off on a challenge):**
  - Keep the stop on an uncleared challenge as it is. That is the back-off.
  - **Short sessions:** at most `--limit 100` items per `ddoloot sync` run, well below the
    199 requests and 13 minutes at which batch 1 failed. At 4 s a run is about 103 wiki
    requests (robots.txt, 1 challenged plain fetch, 1 reload, about 100 items) and about
    7 minutes. Each run is a new process, so it gets a new Chromium, a fresh browser context
    and a new challenge and token (the Page Store never carries escalation over, as ADR 0006
    requires).
  - **Pause 20 minutes between runs**, twice the longest AWS WAF rate window (10 minutes),
    so no window ever holds two runs.
  - **After an uncleared challenge:** stop for the day (at least several hours). Read the
    new `no article …` line: a 405 or 403 means stop until the maintainer decides, and
    consider asking the wiki admins (ADR 0006's admin override).
  - **Crawl delay: recommended, not changed.** If a failure recurs at this cadence, the
    maintainer should raise `crawl_delay_seconds` to 8 in `config/scraper.yaml`. That halves
    the rate to 7.5 a minute, 75 per 10 minutes. This session's rules keep the committed 4.
- **Run cadence (for the continuation commands):**
  ```
  # one run: at most 100 items, then 20 minutes' pause; stop on exit 1 or 3
  .venv/bin/python scripts/guarded_sync.py --log $SCRATCH/run-N.log \
    --scraper-config $SCRATCH/scraper.yaml --page <pages> --limit 100 --max-retries 0
  sleep 1200
  ```
  - With the dry run first (`--dry … --limit 100`) as step 5 requires.
  - Backlog duration: about 7,850 unheld item pages (8,040 from backlog step 1, less 189
    fetched in batch 1), so about 79 runs. Each cycle is about 27 minutes (7 minutes of
    fetching and 20 of pause), so about **36 hours** of wall-clock time and about 8,150
    requests. At 8 runs a day that is about 10 days. With an 8 s delay, a run takes about
    14 minutes and the total is about 45 hours.
- **Files:** `src/page_store/browser.py`, `tests/canned.py`,
  `tests/page_store/test_browser.py`, and this plan. No config change.
- Tests after step 6: 400 passed, 1 skipped.

### Step 7: one probe (live)

- **Worst case, written first:** 1 + 2 + 1 = 4 (robots.txt, a challenged plain fetch, the
  browser's challenge document and its reload). `scripts/guarded_sync.py --dry` agreed:
  update pages read 9, unheld 0; 1 pending row, unheld; k = 1; F = 1; worst case 4. The
  md5 of `data/queue.db` was the same before and after the dry run.
- **Scratch config:** the committed `config/scraper.yaml` plus `max_retries: 0`,
  `browser.consecutive_challenges: 1` and `cache_dir` = the repo's absolute
  `cache/pages`. No other value changed.
- **Command**, on the persistent `data/queue.db`, logged to a file, with the exit code read
  without a pipe:
  `.venv/bin/ddoloot sync --verbose --scraper-config $S/scraper.yaml --page
  Update_8_named_items --limit 1 --max-retries 0 > $S/probe.log 2>&1 && echo "probe exit 0"
  || echo "probe exit $?"`
- **Requests: 4**, from `grep -c 'GET '`, at 14:34:54-14:35:04: robots.txt 200 (plain),
  `Item:Crimson_Chain` 202 (plain), then in the browser the same page's challenge document
  (202, `x-amzn-waf-action: challenge`) and its reload (200). Update pages 5-13 were read
  from the Page Store at no cost. No `api.php`, `index.php` or `Special:` URL.
- **The challenge cleared** in about 1 s. This was about 5 hours after batch 1's stop. The
  step 6 diagnostics logged both documents with their status as intended.
- **Crimson Chain was written:** `catalog-src/items/update-8/armor/…-crimson-chain.json`
  and one registry line (197 → 198). No gaps. `check_catalog('catalog-src')` returns [].
  It is committed with the session summary.
- **Exit code 2** means "completed with failed items remaining". The 5 ingredient rows
  are still `failed` in `data/queue.db`, marked before step 3. One offline
  `ddoloot sync --reset-failed` puts them back to pending. The next run then re-reads them
  from the Page Store as skipped, at no cost (see "To continue").
- **Queue after the probe:** 253 total, 201 complete, 47 pending, 5 failed. 42 of the 47
  pending are unheld.
- Nothing more was fetched.

### Decision 4c: untimed binding

- **Maintainer decision (2026-09-24):** option A of "Proposal: binding with no timing". The
  wiki's untimed `Bound to Character` and `Bound to Account` mean on acquire, so they map
  to the existing `character` and `account` values, the same as the timed on acquire
  forms.
- **Implementation:** two entries in the `binding` map of `catalog/extractor/mappings.yaml`
  (`"bound to account": account`, `"bound to character": character`), with a comment
  naming the decision. No code, schema or enum change. The `, Exclusive` suffix is still
  stripped before the lookup (step 4d), so the untimed Exclusive forms map too, with
  `exclusive` true. `binding_raw` keeps the untimed text, so the reading stays auditable.
  Any other unknown binding text is still an extraction error.
- **Tests (through `extract()`):** `test_row_is_coerced_into_its_field` gains 3 cases
  (untimed Character, untimed Account, untimed Character with Exclusive);
  `test_binding_keeps_the_raw_wiki_text` gains the untimed Account case;
  step 4d's 2 untimed cases in `test_exclusive_is_read_from_the_binding_row` now expect
  `character` and no error. All 6 failed before the mapping change.
- **Files:** `catalog/extractor/mappings.yaml`, `tests/item_extractor/test_extract.py`,
  `tests/item_extractor/extractor_gaps.json` (the 5 `binding` entries removed), this plan,
  and 5 item files under `catalog-src/items`.
- **`catalog-src` diff:** 5 item files (Glorious Obscenity; Phiarlan Veil Shield round and
  angular; Stormreach Marketplace Shield round and angular). In each, `binding` goes from
  null to `account` (Glorious Obscenity) or `character` (the 4 shields), `binding_raw`
  from null to the untimed text, and `extraction_errors` from `{"binding": …}` to `{}`.
  `exclusive` stays false. No registry change and no other file.
- **Offline rerun, Updates 5-13:** the script exits 0 with 0 fetch attempts, the inner sync
  exit code is 2, and 42 unheld pages are skipped (43 before the probe fetched Crimson
  Chain). A second run left `git status` and the diff unchanged, and
  `check_catalog('catalog-src')` returns [].
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 211 | 0 | 7 | 5 | 0 | 5 |
  | After | 211 | 0 | 7 | 0 | 0 | 5 |

- Tests after decision 4c: 404 passed, 1 skipped.

### Decision 4e: alignment DR and Exceptional Fortification

- **Maintainer decision (2026-09-24):** option A of "Proposal: alignment DR and
  Exceptional Fortification" for DR. The bypass is part of the Effect's identity:
  `DR 5/Evil` is the Effect `DR/Evil`, value 5, `flat`, no Bonus Type, and likewise
  `DR 15/Evil`, `DR 5/Good` and `DR 15/Good`. Exceptional Fortification is the Effect
  `Fortification`, 10, `percent`, Bonus Type `exceptional` as the item names it (the
  6 other `exceptional` Effects are named the same way). The tooltip is kept as today.
- **Implementation:** two rules in `catalog/extractor/enchantments.yaml`, placed after
  `clicky` and before `bonus_to`:
  - `alignment_dr`: `^DR\s+(?P<value>\d+)\s*/\s*(?P<bypass>[A-Za-z]+)$`, anchored, with a
    single-word bypass. The name is built by the one new rule key, `name: 'DR/{bypass}'`
    (an effect rule's name as a format string over its captures; without it the `name`
    capture is the name, as before). `config.py` checks at load that the key is only on
    an effect rule and uses only pattern groups. `effects.py` formats it. DR's link and
    tooltip carry no Bonus Type, so it stays null.
  - `bonus_name_paren_percent`: the form `<Bonus> <Name> (+N%)`,
    `^(?P<btype>…)\s+(?P<name>.+?)\s*\(\+(?P<value>\d+)(?P<pct>%)\)$`. The existing
    `btype` capture gives the Bonus Type, which wins over the tooltip's "considered an
    Insight bonus" wording (that sentence was never read as a Bonus Type). After review,
    `btype` is not any first word but an alternation of the 15 Bonus Types already
    extracted in the held `catalog-src` items (Alchemical, Artifact, Competence,
    Deflection, Dodge, Enhancement, Equipment, Exceptional, Implement, Insight, Primal,
    Profane, Resistance, Shield, Vitality). The config has no Bonus Type vocabulary to
    reuse (`bonus_type` patterns take any capitalised word, and `catalog/bonus-types.yaml`
    does not exist yet). So `Greater Fortification (+50%)` or `Improved X (+N%)` is not
    read as Bonus Type `greater` or `improved`. It stays unclassified, as before.
  - **Match check:** all 1041 Effects-list entries on the 202 held `Item:` pages were
    run against both patterns. `alignment_dr` matches exactly the 6 DR entries and
    `bonus_name_paren_percent` exactly `Exceptional Fortification (+10%)`, so the general
    form was kept. No other held entry has `DR`, a `(+` or a bypass-style `/` (the only
    other `/` forms are clicky `Recharged/Day` and the Mythic `and/or` hint). Shield DR
    is an infobox row, not an Effects entry, so it is untouched.
- **Tests (through `extract()`):** `test_alignment_dr_is_an_effect_named_by_its_bypass`
  (the 4 held forms, in the wiki's link-plus-tooltip shape),
  `test_other_dr_forms_are_not_alignment_dr` (4 cases),
  `test_bonus_named_before_a_parenthesised_percent_is_its_bonus_type` (with the Insight
  stacking tooltip) and `test_other_fortification_forms_are_not_read_as_a_parenthesised_percent`
  (3 cases), and `test_a_first_word_that_is_not_a_bonus_type_is_not_read_as_one`
  (`Greater Fortification (+50%)` stays a fallback Effect and is reported as unclassified).
  In `test_config.py`, a `name` using an unknown capture and a `name` on a hint
  rule are both rejected at load. The 5 extraction tests and 2 config tests failed before
  the change.
- **Files:** `catalog/extractor/enchantments.yaml`, `src/item_extractor/config.py`,
  `src/item_extractor/effects.py`, `tests/item_extractor/test_extract.py`,
  `tests/item_extractor/test_config.py`, `tests/item_extractor/extractor_gaps.json` (the
  7 pages' `unclassified_effects` entries removed), this plan, and 7 item files under
  `catalog-src/items`.
- **`catalog-src` diff:** 7 item files, one Effect each: Templar's Bastion, Templar's
  Docent and their Epic versions (`DR/Evil`, 5 or 15), Infested Armor and Epic Infested
  Armor (`DR/Good`, 5 or 15), with `value_kind` `flat` and `bonus_type` still null; and
  Sustaining Symbiont (`Fortification`, 10, `percent`, `exceptional`). Tooltips unchanged.
  No registry change and no other file.
- **Offline rerun, Updates 5-13:** the script exits 0 with 0 fetch attempts, the inner sync
  exit code is 2, and 42 unheld pages are skipped. A second run left `git status` and the
  diff unchanged, and `check_catalog('catalog-src')` returns [].
- **Report totals:**

  | | Lines | Unmapped | Unclassified | Errors | Warnings | Skipped |
  |---|---|---|---|---|---|---|
  | Before | 211 | 0 | 7 | 0 | 0 | 5 |
  | After | 211 | 0 | 0 | 0 | 0 | 5 |

- Tests after decision 4e: 419 passed, 1 skipped.

## Session summary

### Outcomes

| Issue or gap | Outcome | PR | Why |
|---|---|---|---|
| Queue order by `page_name` string | fixed | #20 | Update pages and pending rows go in ascending update number, with non-numbered pages last. A multi-page batch causes no moves. |
| Browser adapter stores a missing page as 200 | fixed | #21 | It reports the final document's status. A 404 is not stored, and the item fails with `page not found (404)`. |
| No-infobox crafting-ingredient pages fail | fixed | #22 | They are not Named Items (CONTEXT.md). With no infobox and a wiki category of `Ingredients` or `Raw ingredients`, the page is skipped: marked complete, a report line, no item file. Any other page with no infobox still fails. |
| 4a Clicky charges and recharge | fixed | #23 | Both forms parse to the spell name, plus the new optional Effect fields `charges` and `recharge_per_day`. ADR 0008's gate now sees the spell name. |
| 4b Wand row `No UMD check for:` | fixed | #24 | New optional field `umd_exempt_classes` (text, as the wiki writes it). |
| 4c Binding with no timing | proposed | #25 | No doc says what untimed means. Awaits the maintainer (options A-D; recommends A, treat it as on acquire). The raw text stays in `extraction_errors.binding`. |
| 4d Exclusive | fixed | #26 | New optional field `exclusive`, from the binding row's `, Exclusive` suffix (49 true), also read when the binding is unmapped. |
| 4e Alignment DR, Exceptional Fortification | proposed | #27 | Effect identity and Bonus Type decisions belong to ADR 0008's review gate. 7 effects stay unclassified. |
| 4f Wiki bug notes in effect text | fixed | #28 | A trailing `( Bug: … )` is stripped before the rules run and kept in the new optional Effect field `note`. |
| 4g `item_type` null for `accessory_untyped` | proposed | #29 | 20 wands and 4 rune arms have no type or slot row. Setting it would mint new values from wiki categories, which is the maintainer's decision. |
| Bulk-run guard and worst-case dry run | fixed | #30 | `scripts/guarded_sync.py`: SIGINT on 3 consecutive 429/5xx, or failures > 10% with at least 3 failed. `--dry` prints `1 + 2F + min(F, k)` with no request. |
| WAF stop on `Crimson_Chain` | investigated, diagnostics added | #31 | Most likely a WAF escalation after sustained load (13 min at a flat 4 s). Token expiry is unlikely, and the 2-document cap is ruled out. Mitigation: short runs with pauses. The crawl delay is unchanged. |
| Probe | done, cleared | this PR | 4 requests. Crimson Chain fetched and written. |

Earlier records corrected:
- The 5 extraction errors are the 5 untimed bindings (4c), not the no-infobox pages. Those
  pages had no report line before step 3.
- Report lines were 205 before this session, not 213.
- For an unmapped binding the raw text is in `extraction_errors.binding`, not
  `binding_raw`.

### Totals

| | Report lines | Unmapped rows | Unclassified effects | Extraction errors | Warnings | Skipped |
|---|---|---|---|---|---|---|
| Before (brief) | 205 | 20 | 60 | 5 | 0 | — |
| After | 211 | 0 | 7 | 5 | 0 | 5 |

- The 7 unclassified effects are step 4e's.
- The 5 extraction errors are step 4c's.
- The 5 skipped lines are the ingredient pages.
- Item files: 197 → 198 (Crimson Chain). Registry lines: 197 → 198. No existing UUID was
  changed.

Tests: 322 passed, 1 skipped before; 400 passed, 1 skipped after.

Requests this session: 4 (the probe).

### To continue (next session)

The cadence is from step 6: at most `--limit 100` per process, a 20-minute pause between
runs, a `--dry` before each run, and stop for the day on an uncleared challenge (guard exit
1 or 3). One batch of about 200 item pages is therefore two runs of `--limit 100`. Keep
`data/queue.db` and `cache/pages`.

1. **Scratch config:** `config/scraper.yaml` plus `max_retries: 0`,
   `browser.consecutive_challenges: 1` and `cache_dir` = the absolute path of the repo's
   `cache/pages`.
2. **Offline:** put the 5 failed ingredient rows back to pending. They are then re-read
   from the Page Store as skipped, at no cost.
   ```
   .venv/bin/ddoloot sync --reset-failed
   ```
3. **Finish Updates 8 and 9:** 47 pending, 42 unheld, so the worst case is 1 + 84 + 1 = 86.
   ```
   .venv/bin/python scripts/guarded_sync.py --dry --scraper-config $SCRATCH/scraper.yaml \
     --page Update_8_named_items Update_9_named_items --limit 100 --max-retries 0
   .venv/bin/python scripts/guarded_sync.py --log $SCRATCH/run-8-9.log \
     --scraper-config $SCRATCH/scraper.yaml \
     --page Update_8_named_items Update_9_named_items --limit 100 --max-retries 0 \
     && echo ok || echo "exit $?"
   ```
   Then verify offline:
   ```
   .venv/bin/python scripts/offline_rerun.py Update_5_named_items Update_6_named_items \
     Update_7_named_items Update_8_named_items Update_9_named_items Update_10_named_items \
     Update_11_named_items Update_12_named_items Update_13_named_items
   UPDATE_GAPS_BASELINE=1 .venv/bin/pytest -q tests/item_extractor -k whole_page_store
   ```
   Then run the four CI checks and `check_catalog('catalog-src')`, confirm that a second
   `offline_rerun.py` leaves `git status` unchanged, and commit.
4. **Updates 14 upward, in ascending N,** in batches of about 200 item pages, using the
   category counts in the backlog plan's step 1:
   - Updates 14-16 (about 110);
   - Update 17 (349, over several runs);
   - Updates 18-25 (about 220);
   - and so on.
   For each batch, repeat until its pages have no pending rows:
   ```
   .venv/bin/python scripts/guarded_sync.py --dry --scraper-config $SCRATCH/scraper.yaml \
     --page <batch pages> --limit 100 --max-retries 0
   .venv/bin/python scripts/guarded_sync.py --log $SCRATCH/run-<N>.log \
     --scraper-config $SCRATCH/scraper.yaml --page <batch pages> --limit 100 \
     --max-retries 0 && echo ok || echo "exit $?"
   sleep 1200
   ```
   - Queue order is numeric now (step 1), so one invocation can list several pages.
   - After each batch, repeat step 3's offline verification over every update page done so
     far, then commit.
   - On exit 1 or 3: read the new `no article …` line. A 405 or 403 means stop until the
     maintainer decides.
   - If the challenge fails again at this cadence, recommend `crawl_delay_seconds: 8` to
     the maintainer. This session did not change it.
5. **Maintainer decisions pending:** the proposals for 4c, 4e and 4g in this plan. Until
   they are decided, those gaps stay in the gaps baseline and `report.jsonl`.
