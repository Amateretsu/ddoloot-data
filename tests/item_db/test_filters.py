"""Tests for item_db._filters._build_where_clause and ItemFilter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from item_db import ItemFilter, ItemRepository
from item_db._filters import _build_where_clause
from item_normalizer.models import DDOItem, ItemSource

# ── _build_where_clause unit tests ───────────────────────────────────────────


class TestBuildWhereClause:
    def test_empty_filter_no_where(self) -> None:
        sql, params = _build_where_clause(ItemFilter())
        assert "WHERE" not in sql
        assert params == []

    def test_name_contains_uses_like_with_wildcards(self) -> None:
        sql, params = _build_where_clause(ItemFilter(name_contains="Sword"))
        assert "LIKE" in sql
        assert params == ["%Sword%"]

    def test_name_contains_transform_applied(self) -> None:
        """name_contains uses a lambda transform — verify the % wrapping."""
        _, params = _build_where_clause(ItemFilter(name_contains="ring"))
        assert params[0] == "%ring%"

    def test_slot_filter(self) -> None:
        sql, params = _build_where_clause(ItemFilter(slot="Back"))
        assert "i.slot = ?" in sql
        assert "Back" in params

    def test_weapon_type_only_joins_weapon_stats(self) -> None:
        sql, params = _build_where_clause(ItemFilter(weapon_type="Longsword"))
        assert "JOIN weapon_stats" in sql
        assert "ws.weapon_type = ?" in sql
        assert "Longsword" in params

    def test_handedness_only_joins_weapon_stats(self) -> None:
        sql, params = _build_where_clause(ItemFilter(handedness="Two-Handed"))
        assert "JOIN weapon_stats" in sql
        assert "ws.handedness = ?" in sql
        assert "Two-Handed" in params

    def test_damage_type_includes_joins_weapon_damage_types(self) -> None:
        sql, params = _build_where_clause(ItemFilter(damage_type_includes="Fire"))
        assert "JOIN weapon_damage_types" in sql
        assert "wdt.damage_type = ?" in sql
        assert "Fire" in params

    def test_armor_type_only_joins_armor_stats(self) -> None:
        sql, params = _build_where_clause(ItemFilter(armor_type="Heavy"))
        assert "JOIN armor_stats" in sql
        assert "ast.armor_type = ?" in sql
        assert "Heavy" in params

    def test_arcane_spell_failure_max_only_joins_armor_stats(self) -> None:
        sql, params = _build_where_clause(ItemFilter(arcane_spell_failure_max=15))
        assert "JOIN armor_stats" in sql
        assert "arcane_spell_failure" in sql
        assert 15 in params

    def test_armor_type_and_arcane_spell_failure_max_combined(self) -> None:
        sql, params = _build_where_clause(
            ItemFilter(armor_type="Light", arcane_spell_failure_max=10)
        )
        assert "JOIN armor_stats" in sql
        assert "ast.armor_type = ?" in sql
        assert "arcane_spell_failure" in sql
        assert "Light" in params
        assert 10 in params

    def test_drops_in_quest_joins_source_quests(self) -> None:
        sql, params = _build_where_clause(ItemFilter(drops_in_quest="The Pit"))
        assert "JOIN source_quests" in sql
        assert "sq.quest_name LIKE ?" in sql
        assert "%The Pit%" in params

    def test_dropped_by_joins_source_dropped_by(self) -> None:
        sql, params = _build_where_clause(ItemFilter(dropped_by="Dragon"))
        assert "JOIN source_dropped_by" in sql
        assert "sdb.monster LIKE ?" in sql
        assert "%Dragon%" in params

    def test_sql_always_selects_distinct_id(self) -> None:
        sql, _ = _build_where_clause(ItemFilter(slot="Head"))
        assert sql.startswith("SELECT DISTINCT i.id FROM items i")

    def test_sql_ordered_by_name(self) -> None:
        sql, _ = _build_where_clause(ItemFilter())
        assert sql.strip().endswith("ORDER BY i.name")

    def test_exclude_race_restricted_adds_null_clause(self) -> None:
        sql, params = _build_where_clause(ItemFilter(exclude_race_restricted=True))
        assert "i.required_race IS NULL" in sql
        assert params == []

    def test_exclude_class_restricted_adds_null_clause(self) -> None:
        sql, params = _build_where_clause(ItemFilter(exclude_class_restricted=True))
        assert "i.required_class IS NULL" in sql
        assert params == []


# ── Integration tests via repo.search() ──────────────────────────────────────


@pytest.fixture
def populated_repo(
    repo: ItemRepository,
    weapon_item: DDOItem,
    armor_item: DDOItem,
    cloak_item: DDOItem,
) -> ItemRepository:
    repo.save_many([weapon_item, armor_item, cloak_item])
    return repo


class TestFilterIntegration:
    def test_name_contains_matches_substring(
        self, populated_repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        results = populated_repo.search(ItemFilter(name_contains="Mantle"))
        assert len(results) == 1
        assert results[0].name == cloak_item.name

    def test_weapon_type_filter(
        self, populated_repo: ItemRepository, weapon_item: DDOItem
    ) -> None:
        results = populated_repo.search(ItemFilter(weapon_type="Longsword"))
        assert len(results) == 1
        assert results[0].name == weapon_item.name

    def test_handedness_filter(
        self, populated_repo: ItemRepository, weapon_item: DDOItem
    ) -> None:
        results = populated_repo.search(ItemFilter(handedness="One-Handed"))
        assert len(results) == 1
        assert results[0].name == weapon_item.name

    def test_damage_type_includes_filter(
        self, populated_repo: ItemRepository, weapon_item: DDOItem
    ) -> None:
        results = populated_repo.search(ItemFilter(damage_type_includes="Slashing"))
        assert len(results) == 1
        assert results[0].name == weapon_item.name

    def test_armor_type_filter(
        self, populated_repo: ItemRepository, armor_item: DDOItem
    ) -> None:
        results = populated_repo.search(ItemFilter(armor_type="Heavy"))
        assert len(results) == 1
        assert results[0].name == armor_item.name

    def test_arcane_spell_failure_max_filter(
        self, populated_repo: ItemRepository, armor_item: DDOItem
    ) -> None:
        # armor_item has arcane_spell_failure=25; 30 should include it
        results = populated_repo.search(ItemFilter(arcane_spell_failure_max=30))
        names = {r.name for r in results}
        assert armor_item.name in names

    def test_arcane_spell_failure_max_excludes_too_high(
        self, populated_repo: ItemRepository, armor_item: DDOItem
    ) -> None:
        # armor_item has arcane_spell_failure=25; max=10 should exclude it
        results = populated_repo.search(ItemFilter(arcane_spell_failure_max=10))
        names = {r.name for r in results}
        assert armor_item.name not in names

    def test_drops_in_quest_filter(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
    ) -> None:
        repo.save(cloak_item)
        results = repo.search(ItemFilter(drops_in_quest="Snitch"))
        assert len(results) == 1
        assert results[0].name == cloak_item.name

    def test_dropped_by_filter(self, repo: ItemRepository) -> None:
        item_with_dropped_by = DDOItem(
            name="Dragon Loot",
            wiki_url="https://ddowiki.com/page/Item:Dragon_Loot",
            scraped_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source=ItemSource(dropped_by=["Red Dragon"]),
        )
        repo.save(item_with_dropped_by)
        results = repo.search(ItemFilter(dropped_by="Dragon"))
        assert len(results) == 1
        assert results[0].name == "Dragon Loot"

    def test_drops_in_quest_no_match(self, populated_repo: ItemRepository) -> None:
        results = populated_repo.search(ItemFilter(drops_in_quest="NonExistentQuest"))
        assert results == []
