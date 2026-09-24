# ddoloot-data

Catalog data and build pipeline for [DDOLoot](https://github.com/Amateretsu/ddoloot). This repo owns the scraper, the item source files, the Named Item UUID registry, Customisation rules, the effects registry, and the signed data bundle the app downloads (ADR 0009 in the app repo).

## Layout

| Path | Purpose |
|---|---|
| `src/`, `scripts/`, `tests/` | scraper, Scraped Item extractor and sync code (moved here from the app repo) |
| `tests/fixtures/pages/` | committed wiki item pages used as test fixtures (CC BY-SA, see `NOTICE`) |
| `catalog-src/items/<update>/<category>/` | Scraped Item JSON per Named Item, filename leads with the UUID (ADR 0006) |
| `catalog/extractor/` | extractor rules: row labels, templates, value maps, Effect classification |
| `catalog/rules/` | option lists, slot compatibility, item overrides (ADR 0007) |
| `catalog/effects/` | effect registry and aliases (ADR 0008) |
| `spec/` | **vendored copy** of the bundle spec; the app repo is the source of truth |

## Commands

Requires Python 3.11+. `pip install -e ".[test,lint]"` installs the `ddoloot` command:

- `ddoloot sync`: discover update pages, fetch item pages (4 s minimum between requests, ADR 0006) and write Scraped Items to `cache/extracted/` (gitignored). See `docs/ddo_sync.md`.
- `ddoloot extract-item "<item name>" [--html PATH]`: print one page's Scraped Item and report from the local page cache or a saved HTML file. It makes no network requests.

## Status

Scaffold. The bundle build, signing, registry bootstrap and review gate are not built yet. Licences: code GPL-3.0 (`LICENSE`), data CC BY-SA 2.5 (`NOTICE`).
