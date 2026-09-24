# catalog_registry

The **UUID registry** is where a Named Item's identity comes from (ADR 0003, app repo, `docs/adr/0003-maintainer-minted-named-item-ids.md`). Each wiki page ID maps to a UUID. The UUID is minted once and never changes, even when the wiki renames the page. The registry is the committed file `catalog-src/registry.jsonl`, and that file is the source of truth: scraper runs only match pages to entries that already exist, or add new ones.

---

## Interface

```python
from catalog_registry import Registry

registry = Registry.load("catalog-src/registry.jsonl")   # a missing file loads empty
named_item_id = registry.id_for(12387, "Item:Boots of Corrosion")
registry.save()                                          # writes the file back, sorted
```

| Member | Description |
|---|---|
| `Registry.load(path) -> Registry` | Reads the registry file. A missing file gives an empty registry, and `save()` creates the file. Raises `ValueError`, naming the file and line, for a malformed line or for two entries that share a page ID or a UUID. |
| `id_for(page_id: int, title: str) -> str` | Returns the UUID for that page ID. A page ID it has not seen gets a new UUID. For a known page ID it updates the stored title to `title` and keeps the UUID. Raises `TypeError` if `page_id` is not an `int` or `title` is not a `str`. |
| `save()` | Writes every entry back to the loaded path. The write is atomic (temporary file, then rename). The same entries always produce the same bytes. |

`id_for` only changes the copy in memory. Nothing is written until `save()`.

---

## Rules

- **Match by page ID only.** A renamed page keeps its UUID, and only the stored title changes. A new page ID gets a new UUID, even when its title matches an existing entry.
- **Entries are permanent.** Entries are never deleted and never given a new UUID. The interface offers no way to do either. Merges and removals are handled by maintainer-curated redirects (ADR 0003), which are not built yet.
- **IDs** are `uuid4`, lowercase and hyphenated. A new ID never collides with one already in the registry.
- **No page ID, no UUID.** A Scraped Item whose `wiki.page_id` is null is never passed to the registry. The caller reports it as an error instead.

---

## File format: `catalog-src/registry.jsonl`

One JSON object per line, with its keys in this order, sorted by `page_id` so diffs stay small:

```json
{"id": "0b6f7a3e-2c1d-4e5f-8a9b-1c2d3e4f5a6b", "page_id": 12387, "title": "Item:Boots of Corrosion"}
```

Titles keep non-ASCII characters as they are (`ensure_ascii=False`). Files are UTF-8, every line ends in `\n`, and an empty registry is an empty file. Blank lines are skipped when the file is loaded. Any key other than `id`, `page_id` and `title` is rejected.
