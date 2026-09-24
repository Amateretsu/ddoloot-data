# Plan: generate the committed item files (`catalog-src/items/`)

This plan follows `2026-09-24-deepen-page-store-and-extractor.md`. That plan left a working
pipeline: the Page Store, discovery, and `extract()` → Scraped Item. What it cannot yet do
is write the committed source that ADR 0006 defines:
`catalog-src/items/<update>/<category>/<uuid>-<slug>.json`. Three things block it:

1. Pages cannot be fetched live, because the wiki WAF-challenges every plain fetch.
2. There is no UUID registry (ADR 0003).
3. There is no writer for the committed layout.

This plan removes all three, then runs a small live pilot that commits the first real item
files.

Vocabulary: `CONTEXT.md` (domain) and the codebase-design skill (module, interface, seam,
adapter). ADRs: `ddoloot-app/docs/adr/0003`, `0006`, `0008`, `0009`.

## Decisions

### Browser fetch (blocker 1)

- The optional extra `ddoloot-data[browser]` (Playwright) is installed in the local `.venv`,
  and Chromium is installed with `playwright install chromium`. CI stays without Playwright:
  the adapter imports it lazily, and tests use the canned transport.
- `config/scraper.yaml` ships with `browser.enabled: true`. Every plain fetch is challenged
  today, so a disabled fallback means no fetch at all. Each run still starts with plain
  fetching, and the ADR 0006 escalation rule switches it to the browser.
- The browser keeps ADR 0006's limits. It uses the same identified user agent and the same
  pace as plain fetching (the existing adapter already passes `user_agent` to
  `new_page`). It uses no proxies, no stealth plugins, no fingerprint spoofing and no
  CAPTCHA services.
- If headless Chromium is still challenged, try once with a headed browser
  (`headless=False`, still a real unmodified Chromium). If that works, it becomes a
  `browser.headless` config key, default `true`, and `scraper.yaml` records the value that
  worked.
- If neither clears the challenge, stop trying. Record the outcome, keep
  `browser.enabled: true` so the run stops loudly on a challenge, and skip step 4. Steps 2
  and 3 are offline and still ship.
- Discovery index: verify `https://ddowiki.com/page/Named_items` live.
  - If it does not list `Update_<N>_named_items` pages, look for a link to the real index
    in the page's content, then follow it. This allows at most 2 extra requests.
  - If there is still no index, add a committed seed list
    (`config/update_pages.yaml`, holding the `Update_<N>_named_items` titles in
    `cache/index.json`) that discovery uses when the index yields nothing. Record which
    path was taken.

### UUID registry (blocker 2, ADR 0003)

- The registry is a new module, `src/catalog_registry/`. Its interface:
  - `Registry.load(path) -> Registry`
  - `registry.id_for(page_id: int, title: str) -> str`, which returns the existing UUID for
    that page ID or mints a new one
  - `registry.save()`
- The file is `catalog-src/registry.jsonl`: one entry per line,
  `{"id": "<uuid4>", "page_id": <int>, "title": "<wiki title>"}`, sorted by `page_id` so
  diffs stay small. It is committed and is the source of truth for identity.
- Matching is by page ID only. A renamed page keeps its UUID, and `id_for` updates the
  stored title. Entries are never deleted or rewritten to a new UUID. Merges and removals
  are maintainer-curated redirects, which are out of scope.
- An item whose `wiki.page_id` is null gets no UUID and no file. It is reported as an error
  in `report.jsonl`, and the run goes on.
- IDs are `uuid4`, lowercase, hyphenated.

### Committed layout writer (blocker 3, ADR 0006)

- `JsonItemWriter` becomes `CatalogWriter`. It is still the adapter behind
  `ScrapedItemWriterProtocol.write(item, report)` and still has an in-memory fake.
  - Item files go to `<items_dir>/<update>/<category>/<uuid>-<slug>.json`, with
    `items_dir` defaulting to `catalog-src/items/`.
  - `report.jsonl` stays gitignored, at `cache/extracted/<update>/report.jsonl`.
  - The per-item JSON under `cache/extracted/` is dropped, because it would duplicate the
    committed file.
- File content is `{"id": <uuid>, **ScrapedItem}`, with `id` first, as JSON with 2-space
  indent, `ensure_ascii=False` and a trailing newline. Only a wiki change or an extractor
  change can alter a file; nothing time-dependent is written.
- `<update>` is the introduced-in update: the lowest `N` of the `Update_<N>_named_items`
  pages that list the item, and `unknown` only when none does. An item listed on several
  update pages ends up with exactly one file.
- `<category>` is the Scraped Item's `category` when it is weapon, armor, shield, jewelry
  or clothing. Anything else, including null, is `other`.
- `<slug>` is the item name, lowercased, with runs of non-alphanumerics turned into `-`,
  trimmed, at most 60 characters, and `item` when empty. The UUID keeps filenames unique.
- There is always exactly one file per UUID. Before writing, the writer finds any existing
  file for that UUID (`*/*/<uuid>-*.json`).
  - If the update, category or slug changed, the file is moved, so git records a rename.
  - When two update pages list an item, the lower update number wins, and `unknown` loses
    to any number.
- The writer needs the registry. The CLI loads `catalog-src/registry.jsonl`, passes it to
  `CatalogWriter`, and saves the registry at the end of every run, including a stopped run.
- Integrity test, committed and run in CI (no network), over whatever is in
  `catalog-src/`:
  - every item file validates as `{"id"} + ScrapedItem`;
  - its `id` is in the registry with the same `page_id`;
  - it is the only file for that UUID;
  - its path matches the update, category and slug rules above.
  With zero files, the test passes trivially.

### Pilot (step 4)

- One live run: `ddoloot sync --page <one update page> --limit 20`. Use an update page
  known to exist (`Update_5_named_items`, from `cache/index.json`), with the browser
  enabled.
- Commit the generated `catalog-src/items/**` and `catalog-src/registry.jsonl`.
- Check the result:
  - the integrity test passes on the real files;
  - `report.jsonl` is reviewed and its totals (unmapped rows, unclassified effects,
    extraction errors, warnings) are recorded in the plan;
  - running the same command a second time changes no committed file. That run is served
    from the Page Store, so it makes no live requests.
- Extractor gaps found in the pilot are recorded, not fixed, unless a fix is a one-line
  mapping or label addition covered by a test through `extract()`.

## Constraints

- ADR 0006: `/page/` reads only, never `/api.php` or `Special:`. Crawl delay at least 4 s,
  one page at a time.
- **Live request budget for the whole run: 40 requests to ddowiki.com**, counting
  robots.txt, index, update and item pages, plain and browser. Step 1 may use at most 12
  and step 4 at most 28. Every live command is gated with `&&` after the step it depends
  on, so a failed setup never falls through to a live fetch.
- No new runtime dependency beyond pydantic, requests, beautifulsoup4, pyyaml, jsonschema,
  tenacity and loguru. Playwright is allowed only as the optional `browser` extra.
- `ddoloot-app` is read-only.
- Tests only at module interfaces, all offline; the autouse network guard stays.

## Steps

Step 0 commits this plan file to main through a PR. Each later step is its own branch and
PR, and leaves ruff, black, isort and pytest green and the CLI working.

1. **Browser fetch works live.** Install the extra and Chromium; enable the browser in
   `config/scraper.yaml`; verify one item page and the discovery index live; apply the
   headed and index fallbacks above only if needed. Docs: `docs/page_store.md`, README
   (browser setup).
2. **UUID registry.** Add the `catalog_registry` module, `catalog-src/registry.jsonl`
   (empty) and interface tests. No network.
3. **Committed layout writer.** `CatalogWriter`, syncer and CLI wiring, the move rules, the
   integrity test, and docs (`docs/ddo_sync.md`, README). No network.
4. **Pilot.** The live run above, committing the first real item files and the registry.
   Skipped, with the reason recorded, if step 1 could not clear the challenge.

## Out of scope

- A bulk run over the full backlog (about 5,000 pages, 5.5 hours or more at 4 s).
- Contacting the wiki admins (ADR 0006's admin override).
- The effects registry and review gate (ADR 0008), the compile stage to `items.json`,
  bundle signing, and bundle spec changes. Those include a damage-multiplier field,
  `slots_status` and `customisation_slots`, all of which the app repo owns.
- Maintainer-curated redirects for merged or removed pages.

## Decisions made during execution

Test baseline before step 1: 245 passed, 1 skipped.

Live request tally (budget 40; step 1 ≤ 12, step 4 ≤ 28): step 1 used 8 (the orchestrator smoke re-runs were served from the Page Store, 0 requests). Running total: 8 of 40.

### Step 1: browser fetch

- Test baseline for step 1 in the venv with Playwright installed: 244 passed, 2 skipped. The
  second skip is the "no Playwright" test. After step 1: 251 passed, 1 skipped.
- Playwright 1.62 and later ship no Chromium for macOS 13, the maintainer's OS; 1.63 failed
  with "does not support chromium on mac13". The `browser` extra is capped at
  `playwright>=1.40,<1.62`, and Playwright 1.60.0 (Chromium revision 1223) is installed in
  `.venv`.
- **Headless works.** Unmodified headless Chromium with the identified user agent cleared
  the WAF challenge on both pages. No headed attempt was needed, so there is no
  `browser.headless` key.
- The browser adapter lets through only wiki `/page/` documents, at most 2 per fetch: the
  challenged document and one reload after the challenge clears. Every other wiki request
  (`load.php`, images, `api.php`) and any further reload is aborted before it is sent. Each
  browser fetch therefore costs 1 or 2 wiki requests, and a challenge cannot loop. The
  stored HTML is the server-rendered article. Extraction of Boots of Corrosion matched the
  retired browser copy exactly, apart from the `wiki` metadata.
- The reload comes about a second after the challenged document, inside one `fetch()`. It
  is the WAF's own step, so our crawl delay does not pace it. Our paced requests stay at
  least 4 s apart.
- Both adapters log every wiki request at DEBUG (`GET … (plain)` / `GET … (browser)`), so
  `--verbose` gives an exact request count.
- A challenge is never retried. Challenge detection runs before the retry rules, even on a
  403 or 5xx, and a test covers this.
- robots.txt is read once per run, and only when the run has to fetch something. A run
  served entirely from the Page Store sends 0 requests.
- **Discovery index: seed-list path taken.** `https://ddowiki.com/page/Named_items`
  renders `Category:Items` and links to no `Update_<N>_named_items` page. Its content links
  to `Category:Named_items_by_update` (82 subcategories), which is probably the real index.
  That link was not followed because it would have exceeded the step 1 budget. Discovery
  now falls back to the committed `config/update_pages.yaml`, which holds the 40 titles
  from `cache/index.json` and is not a complete list.
- The step 1 live runs used a scratch scraper config with `max_retries: 0`, which capped
  the worst case at 4 requests per run. The committed config keeps `max_retries: 3`.
- Step 1 live requests (8): `robots.txt`, then `Item:Boots_of_Corrosion` as plain 202,
  browser challenged, browser reload (stored; `page_id` 12387); `robots.txt`, then
  `Named_items` as plain 202, browser challenged, browser reload (stored).

### Step 2: UUID registry

- **A missing registry file loads as an empty registry.** `save()` creates the file and any
  missing parent directories. The committed `catalog-src/registry.jsonl` is empty (0 bytes),
  and an empty registry saves as an empty file.
- The interface is exactly `Registry.load(path)`, `id_for(page_id, title)` and `save()`,
  with no lookup or iteration method. `id_for` changes only the in-memory copy; nothing
  reaches the file until `save()`.
- Line format: `json.dumps({"id", "page_id", "title"}, ensure_ascii=False)` with default
  separators. Lines are UTF-8, end in `\n` and are sorted by `page_id`. The same entries
  always give the same bytes.
- `save()` is atomic: it writes `<name>.tmp`, then calls `os.replace`.
- `load` is strict. It raises `ValueError`, naming the file and line, for:
  - bad JSON;
  - any keys other than exactly `id`, `page_id` and `title`;
  - an `id` that is not a lowercase hyphenated uuid4;
  - a `page_id` that is not an int (bool is rejected);
  - a `title` that is not a str;
  - two entries with the same `page_id` or the same `id`.
  Blank lines are skipped. There is no custom error class.
- `id_for` raises `TypeError` for a non-int `page_id` (bool and None included) or a non-str
  `title`. Null page IDs are the caller's job (step 3).
- Minting uses `str(uuid.uuid4())` and retries if the new ID clashes with an existing one. A
  new page ID gets a new UUID even when its title matches an existing entry. A rename keeps
  the UUID and updates the stored title.
- `catalog_registry` is added to isort `known_first_party` and to the mypy, coverage and
  vulture paths. `.gitignore` needed no change.
- Tests after step 2: 281 passed, 1 skipped. No network requests.
