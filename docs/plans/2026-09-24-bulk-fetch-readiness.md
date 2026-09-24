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
