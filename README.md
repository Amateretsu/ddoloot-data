# ddoloot-data

Catalog data and build pipeline for [DDOLoot](https://github.com/Amateretsu/ddoloot). This repo owns the scraper, the item source files, the Named Item UUID registry, Customisation rules, the effects registry, and the signed data bundle the app downloads (ADR 0009 in the app repo).

## Layout

| Path | Purpose |
|---|---|
| `src/page_store/` | Page Store: the local copy of wiki pages, fetched under the ADR 0006 policy |
| `src/item_extractor/`, `src/ddo_sync/`, `tests/` | Scraped Item extractor, sync orchestration and the `ddoloot` CLI |
| `config/scraper.yaml` | scraper policy: user agent, crawl delay (4 s minimum), retries, robots.txt, Page Store directory, browser fallback |
| `tests/fixtures/pages/` | committed wiki item pages used as test fixtures (CC BY-SA, see `NOTICE`) |
| `catalog-src/items/<update>/<category>/` | Scraped Item JSON per Named Item, filename leads with the UUID (ADR 0006) |
| `catalog/extractor/` | extractor rules: row labels, templates, value maps, Effect classification |
| `catalog/rules/` | option lists, slot compatibility, item overrides (ADR 0007) |
| `catalog/effects/` | effect registry and aliases (ADR 0008) |
| `spec/` | **vendored copy** of the bundle spec; the app repo is the source of truth |

## Commands

Requires Python 3.11+. `pip install -e ".[test,lint]"` installs the `ddoloot` command:

- `ddoloot sync [--refresh] [--scraper-config PATH] [--queue-db PATH]`: discover update pages, read item pages through the Page Store and write Scraped Items to `cache/extracted/` (gitignored). The Page Store fetches only the pages it does not hold yet. See `docs/ddo_sync.md`.
- `ddoloot sample --count N [--seed N]`: read a sample of queued item pages, spread across update pages, into the Page Store.
- `ddoloot extract-item "<item name>" [--html PATH]`: print one page's Scraped Item and report, from the Page Store or a saved HTML file. It makes no network requests.

Scraper policy lives in `config/scraper.yaml` (see `docs/page_store.md`): a 4 s minimum crawl delay, robots.txt, retries and the pages directory (`cache/pages/`). A WAF challenge stops the run unless the browser fallback is enabled. That fallback needs the optional extra: `pip install -e ".[browser]"` and then `playwright install chromium`.

## Status

Scaffold. The bundle build, signing, registry bootstrap and review gate are not built yet. Licences: code GPL-3.0 (`LICENSE`), data CC BY-SA 2.5 (`NOTICE`).
