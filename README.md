# ddoloot-data

Catalog data and build pipeline for [DDOLoot](https://github.com/Amateretsu/ddoloot). This repo owns the scraper, the item source files, the Named Item UUID registry, Customisation rules, the effects registry, and the signed data bundle the app downloads (ADR 0009 in the app repo).

## Layout

| Path | Purpose |
|---|---|
| `src/`, `scripts/`, `tests/` | scraper and sync code (moved here from the app repo) |
| `catalog-src/items/<update>/<category>/` | normalized per-item JSON, filename leads with the UUID (ADR 0006) |
| `catalog/rules/` | option lists, slot compatibility, item overrides (ADR 0007) |
| `catalog/effects/` | effect registry and aliases (ADR 0008) |
| `spec/` | **vendored copy** of the bundle spec; the app repo is the source of truth |

## Status

Scaffold. The bundle build, signing, registry bootstrap and review gate are not built yet. Licences: code GPL-3.0 (`LICENSE`), data CC BY-SA 2.5 (`NOTICE`).
