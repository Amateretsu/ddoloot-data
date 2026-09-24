# ddoloot-data

Catalog data and build pipeline for [DDOLoot](https://github.com/Amateretsu/ddoloot). This repo owns the scraper, the item source files, the Named Item UUID registry, Customisation rules, the effects registry, and the signed data bundle the app downloads (ADR 0009 in the app repo).

## Layout

| Path | Purpose |
|---|---|
| `src/page_store/` | Page Store: the local copy of wiki pages, fetched under the ADR 0006 policy |
| `src/catalog_registry/` | UUID registry: mints each Named Item's UUID once and keeps it across wiki renames (ADR 0003); see `docs/catalog_registry.md` |
| `src/item_extractor/`, `src/ddo_sync/`, `tests/` | Scraped Item extractor, sync orchestration and the `ddoloot` CLI |
| `config/scraper.yaml` | scraper policy: user agent, crawl delay (4 s minimum), retries, robots.txt, Page Store directory, browser fallback |
| `config/update_pages.yaml` | seed list of `Update_<N>_named_items` pages, used when the named-items index links to none |
| `tests/fixtures/pages/` | committed wiki item pages used as test fixtures (CC BY-SA, see `NOTICE`) |
| `catalog-src/registry.jsonl` | the committed UUID registry: one `{"id", "page_id", "title"}` line per Named Item, sorted by page ID |
| `catalog-src/items/<update>/<category>/` | one `<uuid>-<slug>.json` per Named Item: `{"id"} + ScrapedItem` (ADR 0006), written by `ddoloot sync` and checked in CI by `tests/test_catalog_integrity.py` |
| `catalog/extractor/` | extractor rules: row labels, templates, value maps, Effect classification |
| `catalog/rules/` | option lists, slot compatibility, item overrides (ADR 0007) |
| `catalog/effects/` | effect registry and aliases (ADR 0008) |
| `spec/` | **vendored copy** of the bundle spec; the app repo is the source of truth |

## Commands

Requires Python 3.11+. `pip install -e ".[test,lint]"` installs the `ddoloot` command:

- `ddoloot sync [--refresh] [--scraper-config PATH] [--queue-db PATH]`: discover update pages from the wiki's named-items index page (or the seed list `config/update_pages.yaml` when it links to none), read item pages through the Page Store and write each Scraped Item to the committed `catalog-src/items/<update>/<category>/<uuid>-<slug>.json`, with its UUID from `catalog-src/registry.jsonl` (saved at the end of every run), plus one gitignored `cache/extracted/<update>/report.jsonl` per update page for review. The Page Store fetches only the pages it does not hold yet, and `--refresh` refetches. Nothing uses the MediaWiki API. See `docs/ddo_sync.md`.
- `ddoloot sample --count N [--seed N]`: read a sample of queued item pages, spread across update pages, into the Page Store.
- `ddoloot extract-item "<item name>" [--html PATH]`: print one page's Scraped Item and report, from the Page Store or a saved HTML file. It makes no network requests.

Scraper policy lives in `config/scraper.yaml` (see `docs/page_store.md`): a 4 s minimum crawl delay, robots.txt, retries and the pages directory (`cache/pages/`). The wiki WAF-challenges every plain fetch, so the committed config enables the browser fallback (`browser.enabled: true`): a challenged page is retried in headless Chromium with the same user agent and pace, and a challenge the browser cannot clear stops the run.

### Browser setup

The browser fallback needs the optional extra and a Chromium build:

```bash
pip install -e ".[browser]"      # Playwright, capped below 1.62 (later releases drop macOS 13)
playwright install chromium
```

CI does not install it: Playwright is imported only when a page is first sent to the browser, and tests use canned responses. Add `--verbose` to a `ddoloot` command to log every request sent to the wiki.

## Status

Scaffold. The bundle build, signing, registry bootstrap and review gate are not built yet. Licences: code GPL-3.0 (`LICENSE`), data CC BY-SA 2.5 (`NOTICE`).
