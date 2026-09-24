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
