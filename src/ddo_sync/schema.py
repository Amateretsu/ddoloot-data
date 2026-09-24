"""SQLite schema for the ddo_sync scrape queue database.

Two tables:
  update_pages  — one row per tracked DDO Wiki update page
  scrape_queue  — one row per item discovered on an update page

Apply with:
    conn.executescript(QUEUE_SCHEMA_SQL)
"""

QUEUE_SCHEMA_VERSION: int = 1

QUEUE_SCHEMA_SQL: str = """
PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────────────────────
-- Tracks every update page we have been asked to monitor.
--
-- last_synced_at   : when we last fetched + parsed item links from this page.
-- wiki_modified_at : MediaWiki API revision timestamp for the page.
--
-- A re-sync is needed when wiki_modified_at > last_synced_at, or either is NULL.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS update_pages (
    page_name        TEXT PRIMARY KEY,  -- e.g. "Update_5_named_items"
    page_url         TEXT NOT NULL,     -- e.g. "https://ddowiki.com/page/Update_5_named_items"
    last_synced_at   TEXT,              -- ISO 8601 UTC or NULL (never synced)
    wiki_modified_at TEXT               -- ISO 8601 UTC from MediaWiki API or NULL
);

-- ─────────────────────────────────────────────────────────────────────────────
-- One row per item discovered on an update page.
--
-- Status lifecycle:  pending → in_progress → complete
--                                          → failed   (retry_count incremented)
--                                          → skipped  (no retry)
--
-- UNIQUE (item_name, update_page) prevents re-queuing the same item from
-- the same update page. Use INSERT OR IGNORE to skip duplicates silently.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scrape_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name      TEXT    NOT NULL,
    wiki_url       TEXT    NOT NULL,
    update_page    TEXT    NOT NULL REFERENCES update_pages(page_name) ON DELETE CASCADE,
    status         TEXT    NOT NULL DEFAULT 'pending',
    queued_at      TEXT    NOT NULL,   -- ISO 8601 UTC
    started_at     TEXT,               -- ISO 8601 UTC or NULL
    completed_at   TEXT,               -- ISO 8601 UTC or NULL
    error_message  TEXT,               -- last error text or NULL
    retry_count    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (item_name, update_page)
);

CREATE INDEX IF NOT EXISTS idx_sq_status      ON scrape_queue(status);
CREATE INDEX IF NOT EXISTS idx_sq_update_page ON scrape_queue(update_page);
"""
