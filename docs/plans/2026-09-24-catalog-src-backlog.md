# Plan: scrape the named-item backlog into `catalog-src/items/`

This plan follows `2026-09-24-catalog-src-item-files.md`. That plan left a working live
pipeline (browser fetch, UUID registry, `CatalogWriter`, `check_catalog`) and a 12-item
pilot from `Update_5_named_items`. This plan completes discovery and then works through the
backlog in batches, one PR per batch, within a fixed request budget.

Vocabulary: `CONTEXT.md` (domain) and the codebase-design skill. ADRs:
`ddoloot-app/docs/adr/0003`, `0006`, `0009`. Where the brief and the ADRs are silent, the
option with the smallest interface is chosen and recorded below.

## Budget and live rules

These override anything a step proposes.

- **SESSION_BUDGET = 1200** requests to ddowiki.com for the whole session, counting
  robots.txt, category, update and item pages, plain and browser. The running tally is kept
  under "Decisions made during execution". Requests are counted from `--verbose` logs
  (`grep -c 'GET '`), since both adapters log every wiki request.
- Before every live run, its worst case is written down as `1 + 2F + min(F, k)` (F = pages
  the run must fetch, k = `consecutive_challenges`), and `--limit` is chosen so the worst
  case fits the remaining budget.
- Never call `/api.php`, `index.php` or any `Special:` page. `/page/Category:...` reads are
  allowed. Crawl delay floor 4 s, one page at a time. No proxies, stealth plugins,
  fingerprint spoofing or CAPTCHA services (ADR 0006).
- Every live command is gated with `&&` on what it depends on; exit codes are never read
  through a pipe. Long syncs run in the background and are waited on, not polled.
- One persistent queue, `data/queue.db` (gitignored), for the whole backlog.
  `sync --limit N` processes the next N pending items. No live command is re-run to
  "check" something.
- Every re-run for idempotence or after an extractor fix goes through a no-network harness
  (`ddo_sync.cli.PageStore` patched with transports and sleep that raise / no-op) on a
  fresh queue DB, so only held pages are re-processed.
- **Stop conditions:** the WAF challenge is not cleared; 3 consecutive HTTP 429 or 5xx; a
  run's failed-item rate above 10%; or the remaining budget cannot cover the next run's
  worst case. Stop fetching immediately and record why.
- No new runtime dependency beyond pydantic, requests, beautifulsoup4, pyyaml, jsonschema,
  tenacity and loguru. `ddoloot-app` is read-only. No existing UUID in
  `catalog-src/registry.jsonl` is deleted or rewritten. The committed
  `config/scraper.yaml` politeness values are unchanged; live runs may use a scratch config
  with `max_retries: 0` and `consecutive_challenges: 1`.

## Shipping

Every step is a branch, pushed, a PR opened with `gh`, CI green (`gh pr checks --watch`:
ruff, black --check, isort --check, pytest on 3.11 and 3.12), merged with
`gh pr merge --merge --delete-branch`, and local main pulled before the next step. Each
step runs in its own subagent; the orchestrator verifies, commits and ships.

## Steps

### Step 0: this plan

### Step 1: complete discovery (live, at most 15 requests)

- Fetch `Category:Named_items_by_update` and derive the full list of
  `Update_<N>_named_items` pages (non-numbered pages file under `unknown`) from its
  subcategory links, without fetching each subcategory.
- Verify one derived page that is not in the current seed list with a single fetch.
- Either teach discovery to read that category (tested offline through the discovery
  interface with a committed fixture) or regenerate `config/update_pages.yaml` from it,
  whichever is the smaller change.
- Record the number of update pages, an estimate of the total item count, and the request
  cost of the whole backlog.

### Step 2: local tests survive a growing Page Store (offline)

- The whole-Page-Store extractor test (skipped in CI) fails on any new gap. Change it so
  local runs stay green as the store grows without hiding regressions, e.g. against a
  committed gaps baseline that backlog runs update deliberately.

### Step 3 onwards: the backlog, in batches (live)

- Update pages in ascending N, then `unknown`, so each item lands in its introduced-in
  update first and later listings cause no moves.
- One PR per batch: one update page or several small ones, capped at about 200 item pages
  or the remaining budget, whichever is smaller.
- Per batch, a subagent runs the sync with `--verbose` into a log and reports: exact
  request count; completed, failed, pending counts; item files written per update and
  category, and moves; report.jsonl totals (unmapped rows, unclassified effects, extraction
  errors, warnings); new extractor gaps. It may fix a gap only with a one-line mapping or
  label addition tested through `extract()`, followed by an offline re-run.
- The orchestrator then runs the four checks and `check_catalog('catalog-src')`, runs the
  no-network re-run on a fresh queue and confirms `git status` is clean, appends the batch's
  decisions and tally here, and ships.
- `sync --reset-failed` at most once per batch; items that fail again are recorded by URL.
- Continue until the budget, a stop condition, or the end of the backlog.

### Session summary

Appended when the session stops and shipped as its own PR: update pages completed, partly
done and pending; items per update and category; requests used and still needed; report
totals and gaps by kind; test counts before and after; why the session stopped; the exact
command sequence to continue.

## Decisions made during execution

Test baseline before step 1: 321 passed, 1 skipped.

Live request tally (SESSION_BUDGET 1200): step 1 used 8; step 2 used 0; batch 1 used 201. Running total: 209 of 1200.

### Step 1: discovery

- **Requests: 8 of 15.** Two runs, each worst case 1 + 2 + 1 = 4 (scratch config: committed
  values plus `max_retries: 0`, `consecutive_challenges: 1`). Each was robots.txt, a plain
  202, a browser challenge and a browser reload: `Category:Named_items_by_update`, then
  `Update_4_named_items`. The challenge cleared both times.
- **The category lists 82 subcategories on one page:** `Category:Update_<N>_named_items`
  for N = 0-81 except 66, plus `Category:Unknown_release_named_items`. The listing shows
  each subcategory's member count, so no subcategory was fetched.
- **Category-to-article match:** the stored `Update_5_named_items` has a navigation box
  linking `/page/Update_<N>_named_items` for every N in 6-81 except 66, which confirms those
  articles exist. The single verification fetch went to `Update_4_named_items`, the least
  certain one: the wiki has no such article (`wgArticleId` 0, no item links).
- **Option (b): the seed list was regenerated**, as the smaller change. Discovery already
  falls back to `config/update_pages.yaml`, so step 1 changes data and a comment, not code.
  It holds the 76 pages N = 5-81 except 66, in ascending N.
- **Left out of the list, deliberately:**
  - Update_0 to Update_4: no article, so they have no update page to read. Their categories
    hold 25 item pages. Reaching them would need discovery from categories, which is out
    of scope here.
  - `Unknown_release_named_items`: its category is empty and there is no article. So
    nothing files under `unknown` from discovery in this session.
- **Backlog estimate:** the 76 categories hold 8,050 item pages; Update_5 links 29 items
  against 26 category members, so the queue will hold about 8,980 rows. The largest updates
  are 69 (846), 75 (739), 37 (640), 81 (548), 42 (427) and 61 (378). About 8,040 item pages
  and 75 update pages remain to fetch. Expected cost is about 1 request per page plus about
  3 per run, roughly **8,240 requests** at 200 items a run; the pessimistic formula gives
  about 16,300. SESSION_BUDGET covers about 14% of it.
- **Queue dedupe:** the queue's unique key is `(item_name, update_page)`, so an item listed
  on two update pages is queued twice. The second row costs no request, because the Page
  Store holds the page, and `CatalogWriter` keeps one file under the lowest update.
- **`sync` without `--page` re-reads every update page registered in the queue DB**, so
  batches run with `--page`.
- **Recorded, not fixed: the browser adapter stores a missing page as a 200.**
  `BrowserTransport.fetch` checks only the first `goto` response's status, not the reload's.
  Update_4's "no article" page is now in `cache/pages` (gitignored). A missing `Item:` page
  would be stored and extracted the same way.
- Tests after step 1: 321 passed, 1 skipped. CI lints `src tests`; `ruff check .` and
  `black --check .` also flag `spec/validate_bundle.py`, which fails the same way on main.

### Step 2: gaps baseline

- **Committed baseline:** `tests/item_extractor/extractor_gaps.json` maps each held `Item:`
  page's title to its gaps by kind (`unmapped_rows` labels, `unclassified_effects` texts,
  `extraction_errors` fields, `warnings`, `extraction_failed`). It is sorted JSON, and pages
  with no gaps are left out. It is not under `tests/data/`, because `.gitignore`'s `**data`
  would ignore that path.
- **Update command:** the same test regenerates the baseline, the smallest option (no
  script):
  `UPDATE_GAPS_BASELINE=1 .venv/bin/pytest -q tests/item_extractor -k whole_page_store`.
  It rewrites held pages' entries and keeps the entries of pages not held locally.
- **Entries for unheld pages are not checked.** A held page's entry must match exactly: a
  new gap fails as a regression or an unreviewed gap, and a fixed gap fails as stale until
  the update command removes it. So a fix costs one command, and its diff shows the gap it
  closed.
- Non-item pages (category, update pages) are skipped by the `Item:` title filter, as
  before. The CI skip is unchanged: the test skips when the Page Store holds no pages.
- Seeded from the 13 held item pages: 4 pages, the pilot's two gap kinds (the wand's
  `no umd check for` row and 4 `— N Charges` clickies).
- No other test reads the Page Store. `test_catalog_integrity` and the registry test read
  committed `catalog-src/`, which stays sound as it grows.
- Tests after step 2: 321 passed, 1 skipped.

### Batch 1: Updates 5-13

- **Worst case written before the run:** F = 8 unheld update pages (6-13) + at most 250
  unheld items = 258, so 1 + 2F + min(F, 1) = 518 of the remaining 1,192. The Page Store
  already held `Update_5_named_items` and 13 `Item:` pages.
- **`--limit 250`:** the fresh queue holds only these 9 pages' rows, so the limit is a
  safety cap. It was estimated from 198 category members plus about 10%. The update pages
  actually listed 253 rows (5:29, 6:53, 7:51, 8:37, 9:24, 10:15, 11:11, 12:0, 13:33), so
  250 would have left 3 rows pending anyway.
- **`--max-retries 0`:** `sync_all` resets failed rows with `retry_count < max_retries` at
  the start of every run, and `process_queue` reads each pending row once per run. With 0,
  a failed row is never reset automatically on the persistent queue. Only the one
  `--reset-failed` per batch can reset it. Later batches should keep `--max-retries 0`.
- **Stop guard:** a scratch watchdog tailed the log. It would send SIGINT to the sync on 3
  consecutive 429/5xx, or on more than 22 failed items (10% of about 218). It never fired.
- **Command:** `.venv/bin/ddoloot sync --verbose --scraper-config $S/scraper.yaml --page
  Update_5_named_items ... Update_13_named_items --limit 250 --max-retries 0`. The scratch
  config is the committed values plus `max_retries: 0`, `consecutive_challenges: 1`,
  `cache_dir` = repo `cache/pages`, browser enabled. It used the default `data/queue.db`,
  created by this run.
- **STOP: the WAF challenge was not cleared.** At 09:41, after about 13.5 minutes, the
  browser fetch of `Item:Crimson_Chain` (Update 8) was challenged and did not clear. The run
  stopped with exit 1, and no item was marked failed. Nothing was fetched after that, so
  there was no `--reset-failed` and no re-run.
- **Requests: 201**, from `grep -c 'GET '`:
  - robots.txt;
  - `Update_10_named_items` as plain 202, which switched the rest of the run to the
    browser;
  - 199 browser GETs: the 8 unheld update pages, including one reload after the first
    challenge, then 189 items and the uncleared `Crimson_Chain`.
  - No `api.php`, `index.php` or `Special:` URL.
- **Queue (`sync --status`):** 253 total, 200 complete, 5 failed, 48 pending (24 each on
  Updates 8 and 9, starting with `Crimson_Chain`).
  - 5 of the pending rows are already held and cost 0 next time: the duplicates Beholder
    Plate Armor, Beholder Plate Docent, Epic Envenomed Cloak, Epic Sirocco and Stormsinger
    Cloak, listed on Update 9.
  - About 43 still need fetching.
  - Failed-item rate: 5 of 205 processed, 2.4%.
- **Update pages are read in `page_name` order** (10, 11, 12, 13, 5, 6, ...). So rows
  are queued, and items first written, in that order, and lowest-update-wins then moves
  files down.
- **Files: 185 new item files and 185 registry lines** (12 → 197 lines). No pilot file
  changed.
  - Per update: 5: 16, 6: 53, 7: 49, 8: 9, 9: 2, 10: 13, 11: 10, 13: 33. Update 12 lists no
    items.
  - Per category: armor 27, clothing 31, jewelry 43, other 23, shield 10, weapon 51.
- **Moves:**
  - Chulchannad's Claw, update-11 → update-5, during the live run.
  - Beholder Plate Armor and Docent, update-10 → update-9, during the first offline
    re-run, which processed their held Update 9 rows.
  - Gem of Many Facets and Epic Gem of Many Facets are listed on Updates 6 and 7 and stay
    in one file under update-6.
  - Pending Update 8 and 9 rows can still move items down from update-10, 11 or 13.
- **Failed, recorded by URL.** Each failed with `ExtractionError: no infobox table`. They
  are crafting ingredients or an article, not equipment, and are held in the Page Store:
  - `Item:Token_of_the_Twelve`;
  - `Item:Mark_of_Rhesh_Turakbar`;
  - `Item:Legendary_Mark_of_Rhesh_Turakbar`;
  - `Item:Mark_of_Sheshka`;
  - `Item:Legendary_Mark_of_Sheshka`.
- **Report totals:** after the fix, from each update's `report.jsonl`. The writer keeps
  one line per URL, so there are no duplicates. Update 5 counts only this batch's 16
  items, not the pilot's 12.

  | Update | Items | Unmapped | Unclassified | Errors | Warnings |
  |---|---|---|---|---|---|
  | 5 | 16 | 3 | 7 | 2 | 0 |
  | 6 | 53 | 3 | 7 | 0 | 0 |
  | 7 | 51 | 13 | 28 | 2 | 0 |
  | 8 | 9 | 0 | 4 | 0 | 0 |
  | 9 | 5 | 0 | 0 | 0 | 0 |
  | 10 | 15 | 0 | 2 | 1 | 0 |
  | 11 | 11 | 0 | 1 | 0 | 0 |
  | 13 | 33 | 0 | 7 | 0 | 0 |
  | **Total** | | **19** | **56** | **5** | **0** |

  Before the fix, errors were 20.
- **Gaps by kind, new in this batch:**
  - Clicky charges: 44 in the pilot's `— N Charges` form (mostly Eternal Wands, 50/day),
    plus 4 in a new hyphen form, `Negative Energy Absorption - 5 Charges (Recharged/Day:
    5)`.
  - `DR 5/Evil`-style alignment DR: 6.
  - `Exceptional Fortification (+10%)`: 1.
  - A wiki bug note inside an effect, `Improved Deception +17 ( Bug: ...)`: 1.
  - Unmapped row `no umd check for`: 19 wands.
  - Binding with no timing, `Bound to Character` (4) and `Bound to Account` (1): left
    unmapped, because acquire and equip cannot be told apart.
  - `extraction_failed` (no infobox): the 5 failed pages.
- **Fix applied:** in `catalog/extractor/mappings.yaml`, binding `bound to character on
  acquire , exclusive` → `character`, the pilot's Exclusive fix for the character form.
  - It is tested through `extract()` with a new case in
    `test_row_is_coerced_into_its_field`.
  - The offline re-run changed exactly 15 item files (5: 1, 6: 1, 7: 13) and no registry
    line, and the next re-run changed nothing.
- **Gaps baseline:** regenerated. It only adds 66 pages (4 → 70 entries), with no removals
  or changes.
- **Offline harness (reusable), committed as `scripts/offline_rerun.py`:**
  `.venv/bin/python scripts/offline_rerun.py Update_5_named_items ... Update_13_named_items`,
  run from the repo root. It earns its place because every later batch and the next session
  need it. It overrides `PageStore._fetch`, a private method, so it is a script, not a
  test, and CI does not lint it.
  - It calls `ddo_sync.cli.main(["sync", "--queue-db", <temp>, "--max-retries", "0",
    "--page", ...])` in-process, with `ddo_sync.cli.PageStore` patched to
    `PageStore(config, transport=<raises>, browser=<raises>, sleep=noop)`.
  - An unheld page fails in the temporary queue before robots.txt or any transport is
    touched; the harness reports it as "unheld pages skipped".
  - It prints the transport fetch attempts and exits 1 if there were any.
  - Every run here: 0 attempts, 43 unheld skipped. The last run left `git status` and all
    checksums unchanged.
  - In zsh, pass the titles literally or as `"${P[@]}"`.
- Tests after batch 1: 322 passed, 1 skipped. ruff, black and isort are clean, and
  `check_catalog('catalog-src')` finds 0 problems.
- **Orchestrator verification:** the four checks pass and `check_catalog('catalog-src')`
  returns 0 problems. My own harness run on a fresh queue made 0 fetch attempts and left
  `git diff` byte-identical. The registry diff only adds lines.
- **The session stops fetching here.** The brief lists an uncleared WAF challenge as a stop
  condition, and ADR 0006 favours backing off over pushing through a challenge. So no
  further live run was made in this session, even though 991 requests of the budget
  remain. The log shows the `Crimson_Chain` browser GET at 09:41:16 and the stop at
  09:41:47, about 13.5 minutes and 199 browser requests into the run. Why the challenge
  did not clear is not known. One possibility, not verified, is that the WAF token expired
  or a rate threshold was reached.
