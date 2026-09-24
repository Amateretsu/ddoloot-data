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

### Step 2: Page Store

- The default `cache_dir` is `cache/pages/` (the config says `../cache/pages`, since relative
  paths resolve against the config file's directory). The old `cache/html/` and
  `cache/index.json` are left in place, not migrated, not refetched and no longer read. This
  replaces the plan's "delete and refetch the 40 files", because the run may fetch only a
  handful of live pages. Refetching is left to the operator.
- A `crawl_delay_seconds` below 4 is rejected when the config loads, and the store also
  applies `max(4, delay)`. A larger robots.txt `Crawl-delay` wins.
- robots.txt is fetched once per run through the plain transport, with the configured user
  agent and pacing. A 4xx means no robots.txt, so everything is allowed. A 5xx, network error
  or challenge falls to `robots_fail_open`; fail-closed stops the run.
- robots.txt is evaluated with RFC 9309 semantics (literal prefixes, longest match, `*`/`$`).
  urllib's `RobotFileParser` is not used: it normalises the wiki's `Disallow: /?` to
  `Disallow: /`, which blocks the whole site. The first live smoke run hit exactly this.
- Escalation rule: 5 or more consecutive challenged plain fetches, or strictly more than
  `challenge_ratio` of plain fetches challenged once at least 10 plain fetches have been made
  (`RATIO_MIN_SAMPLE`, fixed in code), switches the rest of the run to the browser. Each
  `PageStore` instance is one run.
- A challenged page is retried once in the browser when enabled. If the browser is also
  challenged, the run stops.
- A challenge is HTTP 202 or any `x-amzn-waf-action` header. The browser adapter reports a
  missing `#mw-content-text` the same way.
- Errors: `FetchError(url, status)` fails that page and the run goes on. `RunStoppedError`
  (and its subclass `ChallengeError`) stops the run. The syncer fetches before
  `mark_in_progress`, so a stopped run leaves the item `pending`.
- Retries cover `TransportError`, 429 and 5xx, up to `max_retries`, waiting
  `delay × 2^attempt`; other 4xx are not retried. A plain paced loop replaces tenacity here.
- `get()` accepts only `https://ddowiki.com/page/<title>` URLs. `/api.php`, `index.php`,
  `Special:`, query strings and other hosts raise `ValueError` before any request.
- The cache is keyed by page title. Filenames are `<slug>-<sha1(title)[:8]>.html`;
  `index.json` maps title → `{url, file, fetched_at, via}`; writes are atomic.
- `config/scraper.yaml` ships with `browser.enabled: false`, so nothing needs Playwright. If
  the fallback is enabled without Playwright, the run stops with install instructions.
- `PageStoreProtocol` exposes only `get()`. Pacing is an injected `sleep` callable, and the
  transports are injected as `transport=` / `browser=`.
- `--refresh` refetches every page the run touches, update pages included.
- `--queue-db PATH` is on `sync` and `sample`, and `--scraper-config PATH` is on all three
  subcommands. `--rate-limit` is removed.
- `sample` picks from every queued item regardless of status, through public repository
  reads. `extract-item NAME` matches held page titles, ignoring case and with or without
  `Item:`.
- `python -m item_extractor` (batch over the retired `cache/html`) is deleted. `scripts/` is
  gone.
- Until step 3, `sync` without `--page`, `sync --discover` and the resync check still reach
  `/api.php` code; the orchestrator did not run them.
- robots.txt user-agent groups: a group applies when its user-agent value appears, ignoring
  case, in our product token (`ddoloot-data`). All matching groups merge; otherwise the `*`
  groups apply. Against the live file only `*` applies: `Crawl-delay: 4`, and `/page/Item:`
  is allowed. The live file is committed as `tests/fixtures/ddowiki_robots.txt`, attributed
  in `NOTICE`.
- Live smoke result: the plain fetch of `Item:Legendary_Gnollish_War_Bow` got a WAF
  challenge. With `browser.enabled: false`, `ddoloot sample` stopped the run with the "browser
  fallback is disabled" error (exit 1), cached nothing and marked nothing failed, as the plan
  requires. The browser adapter was not exercised live: that would need Playwright plus
  Chromium and more live requests than this run allows. Offline, the store was seeded
  through the canned transport, and `ddoloot sample --count 1` (1 held, exit 0) and
  `ddoloot extract-item` both ran end to end against it.
- Live wiki traffic so far: 5 `robots.txt` reads (2 from the pre-guard test runs, 3 from
  smoke runs and diagnosis) and 2 challenged reads of one item page. No further live
  requests are planned.

### Step 3: discovery and results

- The discovery index is `https://ddowiki.com/page/Named_items` (`NAMED_ITEMS_INDEX_URL`),
  **not verified against the live wiki**. Cached item pages link only to
  `Category:Update_N_named_items`. If the index yields no update page, discovery raises
  `UpdatePageError`, so a wrong title fails loudly; `sync --page` works without the index.
  Check this title on the first live run.
- An update page counts only if its link is `/page/Update_<N>_named_items` inside
  `#mw-content-text`. `Category:` links, `…_revamped_named_items`, red links, edit links and
  skin chrome are ignored. Update pages are sorted by update number.
- Item URLs keep the wiki's percent-encoding. Item names are decoded, `_` → space.
- `update_pages.wiki_modified_at` becomes `revision_id INTEGER`, taken from
  `"wgCurRevisionId"` in the update page HTML and written by `mark_page_synced(...,
  revision_id=)`.
- Staleness is gone (`needs_resync` removed); `--refresh` is the only refetch. Every sync
  re-reads each tracked update page (a held copy costs no request), so new links still get
  queued. A refreshed page whose revision changed is logged as old → new.
- The queue schema version lives in `PRAGMA user_version = 2`. An older DB raises
  `QueueSchemaError` ("delete the file and rerun"); there is no migration.
- One repository: `QueueRepository` in `queue_db.py`, with the schema folded in.
  `ScrapeQueueRepository` and `UpdatePageRepository` are deleted. `QueueRepositoryProtocol`
  is removed as it had one adapter, as are `UpdatePageParserProtocol` and the syncer's
  `parser=` and `api_client=` seams. The remaining seams are `PageStoreProtocol` and
  `ScrapedItemWriterProtocol`.
- `sync_update_page` registers an untracked page instead of failing on the foreign key.
  `sync_all(limit=)` replaces the CLI's private `_run_sync`.
- `<update>` travels in the report (`report["update_page"]`), keeping the `write(item,
  report)` signature. The writer maps it: `Update_8_named_items` → `update-8`, anything else
  → `unknown`. An item listed on two update pages is written into both folders.
- The page slug stays the step-1 writer slug (readable, `wiki.url` in the JSON), not the
  Page Store's `<slug>-<hash8>`. Known risk: titles that differ only in punctuation collide.
- `report.jsonl` is one per update folder. Each write replaces that page's line, matched by
  URL, so the file matches the JSON beside it across runs and `--limit` batches. Writes are
  atomic. A line is `{name, url, update_page, **extract report, extraction_errors,
  warnings}`, with `warnings` `[]` until step 5.
- `ddo_sync/discovery.py` absorbs `update_page_parser`. Its interface is
  `discover_update_pages(store, refresh)`, `read_update_page(store, name, refresh) ->
  UpdatePage`, `update_page_url`, `update_slug`.

### Step 4: typed extractor Config

- `load_config(config_dir=None) -> Config` returns one frozen pydantic model (`extra="forbid"`
  throughout) holding `FieldsConfig`, `TemplatesConfig`, `MappingsConfig` and
  `EnchantmentsConfig`. Only `Config` and `ConfigError` are exported. Lookups are
  `cfg.fields.rule_for(label)`, `cfg.fields.is_ignored(label)` and `cfg.template_inputs`.
- `ConfigError` messages are `<file>: <key.path>: <message>`, without pydantic's input dumps.
- Every regex is compiled at load; a bad pattern fails as `bad pattern '<p>': <re error>`.
  Config labels are normalised once at load instead of per page.
- `needs_cell` is removed; it was the only unread knob (every other key was checked and is
  read). A `spread` row may omit `target`, and then its errors are keyed by its first
  normalised label. The shipped YAML keeps the spread targets because they are the step-1
  `extraction_errors` keys. A non-spread row without `target` fails at load.
- `split` in `templates.yaml` is always a list (`SplitPart` / `SplitAll` models).
- One `EntryRule` model with kind-specific optional keys: a set rule requires
  `item_pattern`, a hint rule requires `hint_kind`, and `kind`/`value_kind` are Literals. The
  three `bonus_type` patterns are required.
- Tests: `test_coercers.py` (10) is deleted; the old config tests (7) became 17
  `load_config()` interface tests, and 13 `extract()` tests were added using an
  `item_page(*rows)` builder in the real page shape. Suite 222 → 235.
- Verified: `extract-item --html` output for all six fixtures is byte-identical to `main`.
- Finding, not fixed: `normalize_label` strips a trailing colon only when it is the last
  character, so `"Required Race:\n"` would be an unmapped row. No page seen so far has one.
