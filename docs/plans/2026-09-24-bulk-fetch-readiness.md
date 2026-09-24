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
