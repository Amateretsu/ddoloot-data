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

Live request tally (SESSION_BUDGET 1200): 0 used.
