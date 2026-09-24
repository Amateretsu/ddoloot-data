"""SQLite schema definition for the item_db package.

All tables are created via SCHEMA_SQL. Apply by calling:
    conn.executescript(SCHEMA_SQL)

Foreign key enforcement must be enabled per-connection:
    conn.execute("PRAGMA foreign_keys = ON")

Schema version is tracked in the user_version PRAGMA so future migrations
can detect the current version.
"""

SCHEMA_VERSION: int = 3

SCHEMA_SQL: str = """
PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────────────────────
-- Core item table. Surrogate integer PK; name has a UNIQUE constraint so it
-- can still be used as a human-readable lookup key.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    item_type       TEXT,
    slot            TEXT,
    minimum_level   INTEGER,
    required_race   TEXT,
    required_class  TEXT,
    binding         TEXT,
    material        TEXT,
    hardness        INTEGER,
    durability      INTEGER,
    base_value      INTEGER,
    weight          REAL,
    flavor_text     TEXT,
    wiki_url        TEXT    NOT NULL,
    scraped_at      TEXT    NOT NULL   -- ISO 8601 UTC, e.g. "2026-03-17T12:00:00+00:00"
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Enchantments — one row per enchantment, ordered by position.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS enchantments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    value       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_enchantments_item ON enchantments(item_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Weapon stats — at most one row per item.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weapon_stats (
    item_id             INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    damage_dice         TEXT,
    damage_bonus        INTEGER,
    critical_range      TEXT,
    critical_multiplier INTEGER,
    enchantment_bonus   INTEGER,
    handedness          TEXT,
    proficiency         TEXT,
    weapon_type         TEXT
);

-- damage_type is list[str], stored one row per type to allow filtering.
CREATE TABLE IF NOT EXISTS weapon_damage_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    damage_type TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wdt_item ON weapon_damage_types(item_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Armor stats — at most one row per item.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS armor_stats (
    item_id              INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    armor_type           TEXT,
    armor_bonus          INTEGER,
    max_dex_bonus        INTEGER,
    armor_check_penalty  INTEGER,
    arcane_spell_failure INTEGER
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Named sets — shared across items; name is the natural key.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS named_sets (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS item_named_set (
    item_id     INTEGER NOT NULL REFERENCES items(id)      ON DELETE CASCADE,
    named_set_id INTEGER NOT NULL REFERENCES named_sets(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, named_set_id)
);

CREATE TABLE IF NOT EXISTS set_bonuses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    named_set_id    INTEGER NOT NULL REFERENCES named_sets(id) ON DELETE CASCADE,
    pieces_required INTEGER NOT NULL,
    description     TEXT    NOT NULL,
    UNIQUE (named_set_id, pieces_required)
);
CREATE INDEX IF NOT EXISTS idx_set_bonuses_set ON set_bonuses(named_set_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Item source — where to obtain the item.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS item_source (
    item_id     INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    chest       TEXT,
    crafted_by  TEXT
);

CREATE TABLE IF NOT EXISTS source_quests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    quest_name  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sq_item ON source_quests(item_id);

CREATE TABLE IF NOT EXISTS source_dropped_by (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    monster     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sdb_item ON source_dropped_by(item_id);
"""
