"""Internal read logic for item_db.

_ItemReader reconstructs DDOItem objects from SQL rows using a series of
focused per-section queries keyed on the integer item ID.

Internal module — not part of the public API.
"""

import sqlite3
from datetime import datetime
from typing import Optional

from item_normalizer.models import (
    ArmorStats,
    DDOItem,
    Enchantment,
    ItemSource,
    NamedSet,
    SetBonus,
    WeaponStats,
)


class _ItemReader:
    """Reads DDOItem objects from the database.

    Args:
        conn: An open sqlite3 connection with row_factory = sqlite3.Row.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def read_by_name(self, name: str) -> Optional[DDOItem]:
        """Reconstruct a DDOItem by item name, or None if not found."""
        row = self._conn.execute(
            "SELECT * FROM items WHERE name = ?", (name,)
        ).fetchone()
        return self._reconstruct(row)

    def read_by_id(self, item_id: int) -> Optional[DDOItem]:
        """Reconstruct a DDOItem by surrogate ID, or None if not found."""
        row = self._conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        return self._reconstruct(row)

    # ── Private helpers ─────────────────────────────────────────────────────

    def _reconstruct(self, row: Optional[sqlite3.Row]) -> Optional[DDOItem]:
        if row is None:
            return None

        item_id = row["id"]

        return DDOItem(
            name=row["name"],
            item_type=row["item_type"],
            slot=row["slot"],
            minimum_level=row["minimum_level"],
            required_race=row["required_race"],
            required_class=row["required_class"],
            binding=row["binding"],
            material=row["material"],
            hardness=row["hardness"],
            durability=row["durability"],
            base_value=row["base_value"],
            weight=row["weight"],
            flavor_text=row["flavor_text"],
            wiki_url=row["wiki_url"],
            scraped_at=datetime.fromisoformat(row["scraped_at"]),
            enchantments=self._read_enchantments(item_id),
            weapon_stats=self._read_weapon_stats(item_id),
            armor_stats=self._read_armor_stats(item_id),
            named_set=self._read_named_set(item_id),
            source=self._read_source(item_id),
        )

    def _read_enchantments(self, item_id: int) -> list:
        rows = self._conn.execute(
            "SELECT name, value FROM enchantments WHERE item_id = ? ORDER BY position",
            (item_id,),
        ).fetchall()
        return [Enchantment(name=r["name"], value=r["value"]) for r in rows]

    def _read_weapon_stats(self, item_id: int) -> Optional[WeaponStats]:
        row = self._conn.execute(
            "SELECT * FROM weapon_stats WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None

        dt_rows = self._conn.execute(
            "SELECT damage_type FROM weapon_damage_types WHERE item_id = ? ORDER BY position",
            (item_id,),
        ).fetchall()

        return WeaponStats(
            damage_dice=row["damage_dice"],
            damage_bonus=row["damage_bonus"],
            damage_type=[r["damage_type"] for r in dt_rows],
            critical_range=row["critical_range"],
            critical_multiplier=row["critical_multiplier"],
            enchantment_bonus=row["enchantment_bonus"],
            handedness=row["handedness"],
            proficiency=row["proficiency"],
            weapon_type=row["weapon_type"],
        )

    def _read_armor_stats(self, item_id: int) -> Optional[ArmorStats]:
        row = self._conn.execute(
            "SELECT * FROM armor_stats WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        return ArmorStats(
            armor_type=row["armor_type"],
            armor_bonus=row["armor_bonus"],
            max_dex_bonus=row["max_dex_bonus"],
            armor_check_penalty=row["armor_check_penalty"],
            arcane_spell_failure=row["arcane_spell_failure"],
        )

    def _read_named_set(self, item_id: int) -> Optional[NamedSet]:
        set_row = self._conn.execute(
            """
            SELECT ns.id, ns.name FROM named_sets ns
            JOIN item_named_set ins ON ns.id = ins.named_set_id
            WHERE ins.item_id = ?
            """,
            (item_id,),
        ).fetchone()
        if set_row is None:
            return None

        bonus_rows = self._conn.execute(
            """
            SELECT pieces_required, description FROM set_bonuses
            WHERE named_set_id = ? ORDER BY pieces_required
            """,
            (set_row["id"],),
        ).fetchall()

        return NamedSet(
            name=set_row["name"],
            bonuses=[
                SetBonus(
                    pieces_required=r["pieces_required"], description=r["description"]
                )
                for r in bonus_rows
            ],
        )

    def _read_source(self, item_id: int) -> Optional[ItemSource]:
        src_row = self._conn.execute(
            "SELECT chest, crafted_by FROM item_source WHERE item_id = ?", (item_id,)
        ).fetchone()

        quest_rows = self._conn.execute(
            "SELECT quest_name FROM source_quests WHERE item_id = ? ORDER BY position",
            (item_id,),
        ).fetchall()
        quests = [r["quest_name"] for r in quest_rows]

        monster_rows = self._conn.execute(
            "SELECT monster FROM source_dropped_by WHERE item_id = ? ORDER BY position",
            (item_id,),
        ).fetchall()
        dropped_by = [r["monster"] for r in monster_rows]

        if src_row is None and not quests and not dropped_by:
            return None

        return ItemSource(
            quests=quests,
            chest=src_row["chest"] if src_row else None,
            dropped_by=dropped_by,
            crafted_by=src_row["crafted_by"] if src_row else None,
        )
