# Plan: deepen the Page Store and the Scraped Item extractor

Outcome of the architecture review and grilling session on 2026-09-23/24. Five deepening
candidates merged into one plan. Vocabulary: `CONTEXT.md` (domain) and the codebase-design
skill (module, interface, seam, adapter, locality, leverage).

## Decisions

### Shape of the result

- **One pipeline.** `item_normalizer` and `item_db` are deleted. `item_extractor` is the only
  HTML → Scraped Item module. The SQLite loot database goes with `item_db`; the store is the
  committed JSON per ADR 0006/0009.
- **Scraped Item is a pydantic model**, `extra="forbid"`, every field typed. `load_config`
  checks each `fields.yaml` target exists on the model. Fields:
  - identity: `name`, `wiki {page_id, revision_id, title, url}`, `template`, `category`,
    `item_type`, `equip_slots`
  - requirements: `minimum_level`, `required_race`, `excluded_race`, `required_feat`,
    `required_trait`
  - properties: `binding`, `binding_raw`, `material`, `hardness`, `durability`,
    `base_value_cp`, `weight`, `upgradeable`, `accepts_sentience`, `umd_dc`
  - stats: `weapon_stats`, `armor_stats`, `shield_stats` (typed submodels, null when absent)
  - effects: `effects[]` (name, value, value_kind, bonus_type, tooltip),
    `customisation_hints[]`, `named_set`
  - text: `flavor_text`, `notes`, `tips`, `source {quests, detail, crafted_from, raw}`
  - diagnostics: `extraction_errors: {field: raw_text}`
- **Every field is always written**, null when unknown. Null with no `extraction_errors` entry
  means the wiki row was absent or said "None"; null with an entry means the coercer could
  not parse that raw text.
- **Results layout** (gitignored until the UUID layout exists):
  `cache/extracted/<update>/<page-slug>.json` and `cache/extracted/<update>/report.jsonl`,
  one line per page (name, url, template, unmapped rows, unclassified Effects, extraction
  errors, warnings). `<update>` is `update-8` style, `unknown` when absent.
- **`catalog/normalizer/` is renamed `catalog/extractor/`.**

### Page Store

- New package `page_store` replaces `ddowiki_scraper`. Interface:
  `get(url, refresh=False) -> CachedPage` and `iter_cached()`. `CachedPage` carries html,
  url, title, fetched-at and `via` (plain | browser).
- Owns: plain fetch, robots.txt, crawl-delay pacing (rate limiter becomes an internal seam),
  retries honouring config, WAF challenge detection, browser fallback with the ADR 0006
  escalation rule (5 consecutive or 20% challenged switches the run to browser), filename
  encoding, `index.json`.
- Transport is the injected adapter: HTTP in production, Playwright browser as the second
  production adapter (optional extra `ddoloot-data[browser]`), canned responses in tests.
- Async fetch methods and the `aiohttp` dependency are dropped. ADR 0006 mandates one page
  at a time.
- **Challenge with browser disabled** stops the run with a clear error; nothing is marked
  failed.
- **Cache is disposable.** No migration; the current 40 files are deleted and refetched.
  Filenames are a readable slug plus an 8-char hash of the title (lossless, no collisions).
- **The crawl ledger is the SQLite queue** (`update_pages` + `scrape_queue`). `index.json`
  only says what is cached. The syncer caches every page it fetches; `--refresh` is the only
  way to refetch, no TTL.
- **Scraper policy lives in `config/scraper.yaml`** (repo root, not `catalog/`): `user_agent`,
  `crawl_delay_seconds` (default 4, floor 4 enforced in code), `timeout_seconds`,
  `max_retries`, `respect_robots_txt`, `robots_fail_open`, `cache_dir`, and `browser:`
  (`enabled`, `consecutive_challenges`, `challenge_ratio`). The CLI takes only
  `--scraper-config PATH`.

### Discovery

- Discovery walks `/page/` HTML through the Page Store: named-items index → update pages →
  item pages. `page_discovery.py` and `wiki_api.py` (both `/api.php`, forbidden by ADR 0006)
  are deleted. Last-modified comes from the revision id in the page HTML.
- `QueueRepository` and `ScrapeQueueRepository` merge into one queue module.

### ddo_sync seams

- Protocols kept where two adapters exist: `PageStoreProtocol` (real + in-memory fake) and
  `ScrapedItemWriterProtocol.write(item, report)` (real + in-memory fake).
- The extractor is pure and in-process: the syncer calls `extract()` directly, no protocol.
- `ddoloot normalize-item` becomes `ddoloot extract-item <name>`, printing the Scraped Item
  and its report.
- `scripts/sync_named_items.py` is deleted (duplicate of `ddoloot sync`).
  `scripts/sample_pages.py` becomes `ddoloot sample --count N` backed by the Page Store; the
  stratified pick across update pages moves into `ddo_sync`.

### Extractor internals

- `load_config` returns typed pydantic models for the four YAML files, replacing hand-rolled
  validation. Unknown YAML keys fail at load. `needs_cell` is removed; `spread` fields no
  longer require `target`.
- Effect classification becomes one interface, `classify_effects(td, rules) -> EffectsBlock`
  (effects, customisation hints, named set, unclassified). Routing, the set/set-bonus merge
  in any order, and the "read before stripping tooltips" ordering become implementation.
- An Effect entry with no matching rule is recorded as unclassified in the report; it never
  raises.
- A named set with bonuses but no name is kept, with a warning in the run report.

### Tests

- Deleted: `tests/item_normalizer`, `tests/item_db`, `tests/ddowiki_scraper`, the extractor's
  direct coercer and classifier tests.
- Kept or written at the interface: `tests/page_store` (through `get()`, canned transport,
  tmp cache dir); `tests/item_extractor/test_extract.py` (through `extract()` on committed
  real pages); `tests/ddo_sync` with in-memory fakes; one whole-cache smoke test skipped
  when the cache is absent.
- Committed fixtures in `tests/fixtures/pages/` (CC BY-SA, attributed in `NOTICE`):
  Legendary Gnollish War Bow (weapon), Full Plate of the Ringleader (armor), Breaker of
  Bodies (shield), Bound Elemental Ring of Frost (accessory), Epic Whirling Words (untyped
  accessory), Dark Ressurectionist's Frock Vest (armor with "None" values, zero Effects).
- Python floor raised to 3.11.

## Steps

Step 0 commits the repo as it stands (main has no commits). Then one branch and PR per step;
each leaves tests green and the CLI working end to end.

1. **Retire the old normalizer.** Delete `item_normalizer` and `item_db`; add `ScrapedItem`;
   rename `catalog/normalizer` → `catalog/extractor`; `extract-item` command; fixtures;
   Python 3.11 floor; `extraction_errors` and always-write-every-field.
2. **Page Store.** New `page_store` package with `config/scraper.yaml`, HTTP and browser
   adapters, cache index, escalation rule; syncer and `ddoloot sample` rewired; scripts
   deleted; `aiohttp` dropped.
3. **Discovery and results.** HTML discovery through the Page Store; API modules deleted;
   queue repositories merged; `<update>/` results layout with `report.jsonl`.
4. **Typed extractor Config.** Pydantic rule models; unused knobs removed; direct coercer
   tests replaced by `extract()` tests.
5. **EffectsBlock.** Classifier owns routing; nameless-set warning; no-rule → unclassified.

## Out of scope

- The UUID registry (ADR 0003) and the `catalog-src/items/<update>/<category>/<uuid>-<slug>`
  layout writer.
- The compile stage (registry + rules → bundle `items.json`) and the ADR 0008 review gate.

## Decisions made during execution

### Step 0 (orchestrator)

- Baseline committed as `cadab04` and pushed; `origin` exists, so each step ships as a branch
  plus a GitHub PR merged before the next step starts.
- The only interpreter on the machine was Python 3.9, which is below the 3.11 floor this plan
  sets. The local `.venv` was rebuilt on Python 3.12 using `uv`. `uv` is a developer tool only
  and is not a project dependency. Baseline on 3.12: 644 tests, 643 passed, 1 failed
  (`tests/ddowiki_scraper/test_fetcher.py::TestWikiFetcherSync::test_rate_limiting_between_requests`,
  a timing-sensitive test in a package that step 2 deletes). Source + tests: 14,339 Python lines.
- CI also runs `black --check` and `isort --check`, so every step keeps both green in
  addition to `ruff check src tests` and `pytest -q`.
- Fixture pages are copied from the existing local `cache/html/` rather than fetched live,
  keeping live wiki traffic to a handful of pages for the whole run.
- Correction to the baseline note: the "timing-sensitive" failure was really a live
  `robots.txt` fetch. `WikiFetcher` read `https://ddowiki.com/robots.txt` through urllib. Two
  such requests were made, one in the baseline run and one in step 1's first test run. Step 1
  added an autouse network guard in `tests/conftest.py` that blocks non-local DNS in all
  tests.

### Step 1: retire the old normalizer

- The `ddoloot` CLI is now subcommands (`sync`, `extract-item`; step 2 adds `sample`). A bare
  `ddoloot` exits 2 instead of starting a crawl. `--verbose` is set per subcommand.
- The old `--item` and `--item-override` sync flags are removed; `extract-item` replaces them
  and does no network I/O.
- `extract-item NAME [--html PATH]` prints `{"item", "report"}` JSON. NAME is matched against
  `cache/index.json` names, ignoring case. `--html` reads a file directly, so the committed
  fixtures work without a cache.
- `sync` writes to the fixed path `cache/extracted/`, with no output flag; step 2's
  `config/scraper.yaml` owns paths. `--rate-limit` defaults to 4 and is clamped to at least 4.
- `JsonItemWriter` writes `<page-slug>.json` (URL title, non-alphanumerics → `_`) and appends
  `{name, url, **report}` to `report.jsonl`. Per-update folders are left to step 3.
- `ScrapedItemWriterProtocol.write(item, report)` replaces `ItemRepositoryProtocol` now. The
  in-memory fake lives in the tests. `DDOSyncer` loads `load_config()` once and calls
  `extract()` directly.
- Coercers raise `Unparseable`; the extractor records `extraction_errors[target] = raw_text`,
  keyed by the `fields.yaml` target.
- Spread targets name a primary field so every error has a unique key:
  `weapon_stats.damage_dice`, `weapon_stats.critical_range`, `armor_stats.armor_bonus`.
  `damage_raw` and `critical_raw` are gone.
- Unknown binding text is an extraction error: `binding` and `binding_raw` are both null and
  the raw text is kept in `extraction_errors`.
- `ScrapedItem` keeps `required_class`, which the plan's field list omits, because
  `fields.yaml` maps it and dropping it would lose data.
- A `fields.yaml` target must be a `ScrapedItem` field or a template input (today only
  `slot`); `load_config` rejects anything else.
- List fields default to `[]`, not null. Scalars and submodels default to null.
- `CustomisationHint` is typed as `kind`, `raw`, `name`, `colour`, `text`, `children`.
  `Effect.value_kind` and `binding` stay `str`.
- `scripts/sync_named_items.py` was deleted in step 1, not step 2, because it imported
  `item_db` and `item_normalizer`. `src/ddo_sync/debug_commands.py` went too, as it was
  normalizer-only.
- Finding, not fixed: weapon damage never parses. The wiki writes
  `5.20[1d8+2] + 15 Pierce, Magic`; the damage coercer does not match it, and the model has no
  multiplier field. It now shows up in `extraction_errors` instead of hiding in `damage_raw`.
  A fixture test pins this behaviour. Left for a follow-up.
