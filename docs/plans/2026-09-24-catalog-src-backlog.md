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

Live request tally (SESSION_BUDGET 1200): step 1 used 8. Running total: 8 of 1200.

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
