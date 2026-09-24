# item_db

SQLite persistence layer for `DDOItem` objects. Provides CRUD operations, flexible search filtering, and automatic schema management. No server setup required — the database is a single file.

---

## Quick Start

```python
from item_db import ItemRepository

with ItemRepository("data/loot.db") as repo:
    repo.upsert(item)                          # insert or update by name
    retrieved = repo.get_by_name("Sword of Shadow")
    print(retrieved.minimum_level)
```

---

## ItemRepository

The main interface to the database. Use as a context manager — `open()` and `close()` are called automatically.

```python
from item_db import ItemRepository

# Context manager (recommended)
with ItemRepository("data/loot.db") as repo:
    ...

# Manual lifecycle
repo = ItemRepository("data/loot.db")
repo.open()
try:
    ...
finally:
    repo.close()

# In-memory database (useful for testing)
with ItemRepository(":memory:") as repo:
    ...
```

### Write Methods

#### `save(item: DDOItem) → int`

Insert a new item. Returns the assigned integer ID.

```python
item_id = repo.save(item)
```

**Raises:** `DuplicateItemError` if an item with the same name already exists.

#### `upsert(item: DDOItem) → int`

Insert or update by item name. Returns the item's integer ID. Safe to call repeatedly as items are re-scraped.

```python
item_id = repo.upsert(item)
```

#### `update_item(item_id: int, item: DDOItem) → None`

Replace all data for the given ID with the new item.

```python
repo.update_item(42, updated_item)
```

#### `delete_by_id(item_id: int) → None`

Delete an item by its integer ID. All related rows (enchantments, weapon stats, etc.) are cascade-deleted.

#### `delete_by_name(name: str) → None`

Delete an item by its unique name.

### Read Methods

#### `get_by_id(item_id: int) → DDOItem`

**Raises:** `ItemNotFoundError` if no item with that ID exists.

#### `get_by_name(name: str) → DDOItem`

**Raises:** `ItemNotFoundError` if no item with that name exists.

#### `list_all() → list[DDOItem]`

Returns every item in the database, unordered.

#### `search(filter: ItemFilter) → list[DDOItem]`

Returns items matching all criteria in `filter`. Any field left as `None` is not used as a constraint.

```python
from item_db import ItemRepository, ItemFilter

with ItemRepository("data/loot.db") as repo:
    cloaks = repo.search(ItemFilter(slot="Back", minimum_level=20))
    for item in cloaks:
        print(item.name)
```

### Metadata Methods

#### `count() → int`

Total number of items in the database.

#### `exists_by_name(name: str) → bool`

Returns `True` if an item with that name exists.

---

## ItemFilter

A dataclass used with `repo.search()`. All fields are optional — only non-`None` fields are applied as WHERE conditions.

```python
from item_db import ItemFilter

# Items that are cloaks at or above level 20
f = ItemFilter(slot="Back", minimum_level=20)

# Weapons of a specific type
f = ItemFilter(item_type="Longsword")
```

Available filter fields mirror the top-level fields of `DDOItem` (slot, minimum_level, item_type, binding, material, required_race, required_class).

---

## Database Schema

Schema is applied automatically on first `open()` and is idempotent (`CREATE TABLE IF NOT EXISTS`). Foreign key enforcement is enabled per-connection.

```sql
-- Core item table. Name has a UNIQUE constraint.
CREATE TABLE items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    item_type       TEXT,
    slot            TEXT,
    minimum_level   INTEGER,
    ...
    wiki_url        TEXT    NOT NULL,
    scraped_at      TEXT    NOT NULL   -- ISO 8601 UTC
);

-- One row per enchantment, ordered by position.
CREATE TABLE enchantments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    value    INTEGER            -- integer tier/magnitude, or NULL
);

-- Weapon stats (at most one row per item)
CREATE TABLE weapon_stats ( item_id INTEGER PRIMARY KEY, ... );

-- Damage types stored as one row per type (supports filtering)
CREATE TABLE weapon_damage_types ( item_id, position, damage_type );

-- Armor stats (at most one row per item)
CREATE TABLE armor_stats ( item_id INTEGER PRIMARY KEY, ... );

-- Named sets shared across items
CREATE TABLE named_sets ( id, name UNIQUE );
CREATE TABLE item_named_set ( item_id, named_set_id );
CREATE TABLE set_bonuses ( named_set_id, pieces_required, description );

-- Item source / acquisition
CREATE TABLE item_source ( item_id, chest, crafted_by );
CREATE TABLE source_quests ( item_id, position, quest_name );
```

Current schema version: **3** (tracked in SQLite `PRAGMA user_version`).

---

## Exceptions

All exceptions inherit from `ItemDbError`.

| Exception | When raised |
|---|---|
| `ItemDbError` | Base class for all database errors |
| `ItemNotFoundError` | `get_by_id` or `get_by_name` found no matching row |
| `DuplicateItemError` | `save` attempted to insert a name that already exists |
| `SchemaError` | Schema could not be applied (corrupt database, version mismatch) |

```python
from item_db import ItemNotFoundError, DuplicateItemError

try:
    item = repo.get_by_name("Nonexistent Item")
except ItemNotFoundError:
    print("Item not in database yet")

try:
    repo.save(item)
except DuplicateItemError:
    repo.upsert(item)   # use upsert instead
```
