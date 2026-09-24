"""Internal write logic for item_db.

_ItemWriter handles all INSERT and DELETE operations for a single DDOItem.
The caller (ItemRepository) is responsible for connection lifecycle and
transactions.

Internal module — not part of the public API.
"""

import sqlite3

from item_normalizer.models import DDOItem


class _ItemWriter:
    """Writes a DDOItem to the database within an existing connection.

    All methods assume a transaction is already open; they do not commit.

    Args:
        conn: An open sqlite3 connection with foreign_keys = ON.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def delete_by_name(self, name: str) -> bool:
        """Delete an item by name. FK cascade removes all child rows.

        Args:
            name: Item name (unique constraint).

        Returns:
            True if a row was deleted, False if the name did not exist.
        """
        cursor = self._conn.execute("DELETE FROM items WHERE name = ?", (name,))
        return cursor.rowcount > 0

    def delete_by_id(self, item_id: int) -> bool:
        """Delete an item by surrogate ID. FK cascade removes all child rows.

        Args:
            item_id: The integer primary key.

        Returns:
            True if a row was deleted, False if the ID did not exist.
        """
        cursor = self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        return cursor.rowcount > 0

    def write(self, item: DDOItem) -> int:
        """Insert all rows for a DDOItem. The item must not already exist.

        Args:
            item: DDOItem to persist.

        Returns:
            The integer ID assigned to the new item row.
        """
        item_id = self._insert_item(item)
        self._insert_enchantments(item_id, item)
        if item.weapon_stats is not None:
            self._insert_weapon_stats(item_id, item)
        if item.armor_stats is not None:
            self._insert_armor_stats(item_id, item)
        if item.named_set is not None:
            self._insert_named_set(item_id, item)
        if item.source is not None:
            self._insert_source(item_id, item)
        return item_id

    # ── Private helpers ─────────────────────────────────────────────────────

    def _insert_item(self, item: DDOItem) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO items (
                name, item_type, slot, minimum_level, required_race,
                required_class, binding, material, hardness, durability,
                base_value, weight, flavor_text, wiki_url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.name,
                item.item_type,
                item.slot,
                item.minimum_level,
                item.required_race,
                item.required_class,
                item.binding,
                item.material,
                item.hardness,
                item.durability,
                item.base_value,
                item.weight,
                item.flavor_text,
                item.wiki_url,
                item.scraped_at.isoformat(),
            ),
        )
        return cursor.lastrowid

    def _insert_enchantments(self, item_id: int, item: DDOItem) -> None:
        self._conn.executemany(
            "INSERT INTO enchantments (item_id, position, name, value) VALUES (?, ?, ?, ?)",
            [
                (item_id, pos, enc.name, enc.value)
                for pos, enc in enumerate(item.enchantments)
            ],
        )

    def _insert_weapon_stats(self, item_id: int, item: DDOItem) -> None:
        ws = item.weapon_stats
        self._conn.execute(
            """
            INSERT INTO weapon_stats (
                item_id, damage_dice, damage_bonus, critical_range,
                critical_multiplier, enchantment_bonus, handedness,
                proficiency, weapon_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                ws.damage_dice,
                ws.damage_bonus,
                ws.critical_range,
                ws.critical_multiplier,
                ws.enchantment_bonus,
                ws.handedness,
                ws.proficiency,
                ws.weapon_type,
            ),
        )
        self._conn.executemany(
            "INSERT INTO weapon_damage_types (item_id, position, damage_type) VALUES (?, ?, ?)",
            [(item_id, pos, dt) for pos, dt in enumerate(ws.damage_type)],
        )

    def _insert_armor_stats(self, item_id: int, item: DDOItem) -> None:
        a = item.armor_stats
        self._conn.execute(
            """
            INSERT INTO armor_stats (
                item_id, armor_type, armor_bonus, max_dex_bonus,
                armor_check_penalty, arcane_spell_failure
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                a.armor_type,
                a.armor_bonus,
                a.max_dex_bonus,
                a.armor_check_penalty,
                a.arcane_spell_failure,
            ),
        )

    def _insert_named_set(self, item_id: int, item: DDOItem) -> None:
        ns = item.named_set

        # Insert the set itself — another item may have already created it.
        self._conn.execute(
            "INSERT OR IGNORE INTO named_sets (name) VALUES (?)",
            (ns.name,),
        )
        row = self._conn.execute(
            "SELECT id FROM named_sets WHERE name = ?", (ns.name,)
        ).fetchone()
        named_set_id = row["id"]

        self._conn.execute(
            "INSERT OR IGNORE INTO item_named_set (item_id, named_set_id) VALUES (?, ?)",
            (item_id, named_set_id),
        )

        # Insert set bonuses — may already exist from another item in the same set.
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO set_bonuses (named_set_id, pieces_required, description)
            VALUES (?, ?, ?)
            """,
            [(named_set_id, b.pieces_required, b.description) for b in ns.bonuses],
        )

    def _insert_source(self, item_id: int, item: DDOItem) -> None:
        src = item.source
        self._conn.execute(
            "INSERT INTO item_source (item_id, chest, crafted_by) VALUES (?, ?, ?)",
            (item_id, src.chest, src.crafted_by),
        )
        self._conn.executemany(
            "INSERT INTO source_quests (item_id, position, quest_name) VALUES (?, ?, ?)",
            [(item_id, pos, q) for pos, q in enumerate(src.quests)],
        )
        self._conn.executemany(
            "INSERT INTO source_dropped_by (item_id, position, monster) VALUES (?, ?, ?)",
            [(item_id, pos, m) for pos, m in enumerate(src.dropped_by)],
        )
