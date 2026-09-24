"""Tests for ItemRepository — save, upsert, get, delete, search, and helpers."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from item_db import ItemFilter, ItemRepository
from item_db._writer import _ItemWriter
from item_db.exceptions import (
    DuplicateItemError,
    ItemDbError,
    ItemNotFoundError,
    SchemaError,
)
from item_normalizer.models import DDOItem, Enchantment

# ── Lifecycle ────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_context_manager_opens_and_closes(self) -> None:
        with ItemRepository(":memory:") as repo:
            assert repo.count() == 0

    def test_open_is_idempotent(self) -> None:
        with ItemRepository(":memory:") as repo:
            repo.open()
            repo.open()
            assert repo.count() == 0

    def test_close_is_idempotent(self) -> None:
        repo = ItemRepository(":memory:")
        repo.close()
        repo.close()

    def test_lazy_open_on_first_use(self) -> None:
        repo = ItemRepository(":memory:")
        assert repo.count() == 0
        repo.close()


# ── Save ────────────────────────────────────────────────────────────────────


class TestSave:
    def test_save_minimal_item(
        self, repo: ItemRepository, minimal_item: DDOItem
    ) -> None:
        item_id = repo.save(minimal_item)
        assert isinstance(item_id, int)
        assert item_id >= 1
        assert repo.count() == 1

    def test_save_cloak(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        assert repo.exists(cloak_item.name)

    def test_save_duplicate_raises(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        with pytest.raises(DuplicateItemError):
            repo.save(cloak_item)

    def test_save_type_error(self, repo: ItemRepository) -> None:
        with pytest.raises(TypeError):
            repo.save("not a DDOItem")  # type: ignore[arg-type]


# ── Upsert ───────────────────────────────────────────────────────────────────


class TestUpsert:
    def test_upsert_new_item(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        item_id = repo.upsert(cloak_item)
        assert isinstance(item_id, int)
        assert repo.count() == 1

    def test_upsert_replaces_existing(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.upsert(cloak_item)

        # Build updated version with a changed field
        updated = DDOItem(**{**cloak_item.model_dump(), "minimum_level": 25})
        repo.upsert(updated)

        assert repo.count() == 1
        assert repo.get(cloak_item.name).minimum_level == 25

    def test_upsert_replaces_enchantments(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.upsert(cloak_item)

        fewer_enchants = DDOItem(
            **{
                **cloak_item.model_dump(),
                "enchantments": [Enchantment(name="Resistance", value=5)],
            }
        )
        repo.upsert(fewer_enchants)

        restored = repo.get(cloak_item.name)
        assert len(restored.enchantments) == 1

    def test_upsert_type_error(self, repo: ItemRepository) -> None:
        with pytest.raises(TypeError):
            repo.upsert({"name": "bad"})  # type: ignore[arg-type]


# ── Delete ───────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_existing(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        repo.delete(cloak_item.name)
        assert repo.count() == 0

    def test_delete_not_found(self, repo: ItemRepository) -> None:
        with pytest.raises(ItemNotFoundError):
            repo.delete("Nonexistent Item")

    def test_delete_cascades_enchantments(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        repo.delete(cloak_item.name)
        # Should not raise — item is gone
        assert not repo.exists(cloak_item.name)


# ── Save Many ────────────────────────────────────────────────────────────────


class TestSaveMany:
    def test_save_many_all_success(
        self, repo: ItemRepository, cloak_item: DDOItem, weapon_item: DDOItem
    ) -> None:
        saved, failures = repo.save_many([cloak_item, weapon_item])
        assert saved == 2
        assert failures == []
        assert repo.count() == 2

    def test_save_many_partial_failure(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        # Second save of same item upserts (succeeds), so use save_many with non-DDOItem
        _saved, failures = repo.save_many([cloak_item, "bad"])  # type: ignore[list-item]
        assert len(failures) == 1
        assert failures[0][0] == "bad"
        assert isinstance(failures[0][1], TypeError)


# ── Get ──────────────────────────────────────────────────────────────────────


class TestGet:
    def test_get_existing(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        fetched = repo.get(cloak_item.name)
        assert fetched.name == cloak_item.name

    def test_get_by_id(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        item_id = repo.save(cloak_item)
        fetched = repo.get_by_id(item_id)
        assert fetched.name == cloak_item.name

    def test_get_by_id_or_none_missing(self, repo: ItemRepository) -> None:
        assert repo.get_by_id_or_none(9999) is None

    def test_get_id(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        item_id = repo.save(cloak_item)
        assert repo.get_id(cloak_item.name) == item_id

    def test_get_id_missing(self, repo: ItemRepository) -> None:
        assert repo.get_id("Ghost Item") is None

    def test_get_not_found(self, repo: ItemRepository) -> None:
        with pytest.raises(ItemNotFoundError):
            repo.get("Nonexistent")

    def test_get_or_none_returns_none(self, repo: ItemRepository) -> None:
        assert repo.get_or_none("Nonexistent") is None

    def test_get_or_none_returns_item(
        self, repo: ItemRepository, minimal_item: DDOItem
    ) -> None:
        repo.save(minimal_item)
        assert repo.get_or_none(minimal_item.name) is not None


# ── Round-trip fidelity ──────────────────────────────────────────────────────


class TestRoundTrip:
    def test_cloak_round_trip(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        fetched = repo.get(cloak_item.name)

        assert fetched.name == cloak_item.name
        assert fetched.slot == cloak_item.slot
        assert fetched.minimum_level == cloak_item.minimum_level
        assert fetched.material == cloak_item.material
        assert fetched.hardness == cloak_item.hardness
        assert fetched.durability == cloak_item.durability
        assert fetched.weight == cloak_item.weight
        assert fetched.flavor_text == cloak_item.flavor_text
        assert fetched.binding == cloak_item.binding
        assert fetched.wiki_url == cloak_item.wiki_url
        assert fetched.scraped_at == cloak_item.scraped_at

    def test_enchantments_round_trip(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        fetched = repo.get(cloak_item.name)

        assert len(fetched.enchantments) == len(cloak_item.enchantments)
        for original, stored in zip(cloak_item.enchantments, fetched.enchantments):
            assert stored.name == original.name
            assert stored.value == original.value

    def test_enchantment_order_preserved(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        fetched = repo.get(cloak_item.name)
        names = [e.name for e in fetched.enchantments]
        assert names == [e.name for e in cloak_item.enchantments]

    def test_weapon_stats_round_trip(
        self, repo: ItemRepository, weapon_item: DDOItem
    ) -> None:
        repo.save(weapon_item)
        fetched = repo.get(weapon_item.name)

        ws = fetched.weapon_stats
        assert ws is not None
        assert ws.damage_dice == "1d8"
        assert ws.damage_bonus == 5
        assert ws.critical_range == "19-20"
        assert ws.critical_multiplier == 2
        assert ws.enchantment_bonus == 5
        assert ws.handedness == "One-Handed"
        assert ws.weapon_type == "Longsword"
        assert "Slashing" in ws.damage_type
        assert "Magic" in ws.damage_type

    def test_weapon_damage_type_order(
        self, repo: ItemRepository, weapon_item: DDOItem
    ) -> None:
        repo.save(weapon_item)
        fetched = repo.get(weapon_item.name)
        assert fetched.weapon_stats.damage_type == ["Slashing", "Magic"]

    def test_armor_stats_round_trip(
        self, repo: ItemRepository, armor_item: DDOItem
    ) -> None:
        repo.save(armor_item)
        fetched = repo.get(armor_item.name)

        a = fetched.armor_stats
        assert a is not None
        assert a.armor_type == "Heavy"
        assert a.armor_bonus == 9
        assert a.max_dex_bonus == 3
        assert a.armor_check_penalty == -3
        assert a.arcane_spell_failure == 25

    def test_named_set_round_trip(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        fetched = repo.get(cloak_item.name)

        ns = fetched.named_set
        assert ns is not None
        assert ns.name == "Thelanis Fairy Tale"
        assert len(ns.bonuses) == 1
        assert ns.bonuses[0].pieces_required == 2

    def test_source_round_trip(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        fetched = repo.get(cloak_item.name)

        src = fetched.source
        assert src is not None
        assert "The Snitch" in src.quests
        assert "The Spinner of Shadows" in src.quests

    def test_source_quest_order(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        fetched = repo.get(cloak_item.name)
        assert fetched.source.quests == cloak_item.source.quests

    def test_minimal_item_round_trip(
        self, repo: ItemRepository, minimal_item: DDOItem
    ) -> None:
        repo.save(minimal_item)
        fetched = repo.get(minimal_item.name)

        assert fetched.name == minimal_item.name
        assert fetched.enchantments == []
        assert fetched.weapon_stats is None
        assert fetched.armor_stats is None
        assert fetched.named_set is None
        assert fetched.source is None

    def test_no_weapon_stats_for_cloak(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        assert repo.get(cloak_item.name).weapon_stats is None

    def test_no_armor_stats_for_weapon(
        self, repo: ItemRepository, weapon_item: DDOItem
    ) -> None:
        repo.save(weapon_item)
        assert repo.get(weapon_item.name).armor_stats is None


# ── Helpers ──────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_list_names_empty(self, repo: ItemRepository) -> None:
        assert repo.list_names() == []

    def test_list_names_sorted(
        self, repo: ItemRepository, cloak_item: DDOItem, weapon_item: DDOItem
    ) -> None:
        repo.save_many([weapon_item, cloak_item])
        names = repo.list_names()
        assert names == sorted(names)

    def test_count(
        self, repo: ItemRepository, cloak_item: DDOItem, weapon_item: DDOItem
    ) -> None:
        repo.save_many([cloak_item, weapon_item])
        assert repo.count() == 2

    def test_exists_true(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        assert repo.exists(cloak_item.name) is True

    def test_exists_false(self, repo: ItemRepository) -> None:
        assert repo.exists("Ghost Item") is False

    def test_get_scraped_at(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        ts = repo.get_scraped_at(cloak_item.name)
        assert ts == cloak_item.scraped_at

    def test_get_scraped_at_not_found(self, repo: ItemRepository) -> None:
        assert repo.get_scraped_at("Ghost Item") is None


# ── Search ───────────────────────────────────────────────────────────────────


class TestSearch:
    def _populate(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        repo.save_many([cloak_item, weapon_item, armor_item, minimal_item])

    def test_empty_filter_returns_all(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        results = repo.search(ItemFilter())
        assert len(results) == 4

    def test_filter_by_slot(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        results = repo.search(ItemFilter(slot="Back"))
        assert len(results) == 1
        assert results[0].name == cloak_item.name

    def test_filter_by_minimum_level_max(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        # minimal_item has no minimum_level (NULL) — included by the IS NULL OR check
        results = repo.search(ItemFilter(minimum_level_max=15))
        names = {r.name for r in results}
        assert weapon_item.name in names  # ml=15
        assert cloak_item.name not in names  # ml=20

    def test_filter_by_weapon_type(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        results = repo.search(ItemFilter(weapon_type="Longsword"))
        assert len(results) == 1
        assert results[0].name == weapon_item.name

    def test_filter_by_armor_type(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        results = repo.search(ItemFilter(armor_type="Heavy"))
        assert len(results) == 1
        assert results[0].name == armor_item.name

    def test_filter_by_enchantment(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        results = repo.search(ItemFilter(has_enchantment="Vorpal"))
        assert len(results) == 1
        assert results[0].name == weapon_item.name

    def test_filter_by_named_set(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        results = repo.search(ItemFilter(named_set="Thelanis Fairy Tale"))
        assert len(results) == 1
        assert results[0].name == cloak_item.name

    def test_filter_by_name_contains(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        results = repo.search(ItemFilter(name_contains="Mantle"))
        assert len(results) == 1
        assert results[0].name == cloak_item.name

    def test_combined_filters(
        self,
        repo: ItemRepository,
        cloak_item: DDOItem,
        weapon_item: DDOItem,
        armor_item: DDOItem,
        minimal_item: DDOItem,
    ) -> None:
        self._populate(repo, cloak_item, weapon_item, armor_item, minimal_item)
        # Slot=Back AND min_level <= 20 → only the cloak
        results = repo.search(ItemFilter(slot="Back", minimum_level_max=20))
        assert len(results) == 1

    def test_no_match_returns_empty(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        results = repo.search(ItemFilter(slot="Head"))
        assert results == []


# ── Convenience finders ───────────────────────────────────────────────────────


class TestFinders:
    def test_find_by_enchantment(
        self, repo: ItemRepository, cloak_item: DDOItem, weapon_item: DDOItem
    ) -> None:
        repo.save_many([cloak_item, weapon_item])
        names = repo.find_by_enchantment("Resistance")
        assert cloak_item.name in names
        assert weapon_item.name not in names

    def test_find_by_enchantment_partial(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        # "Resist" should match "Resistance"
        names = repo.find_by_enchantment("Resist")
        assert cloak_item.name in names

    def test_find_by_set(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        items = repo.find_by_set("Thelanis Fairy Tale")
        assert len(items) == 1
        assert items[0].name == cloak_item.name

    def test_find_by_set_no_match(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        repo.save(cloak_item)
        assert repo.find_by_set("Nonexistent Set") == []

    def test_find_by_quest(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        repo.save(cloak_item)
        names = repo.find_by_quest("Snitch")
        assert cloak_item.name in names

    def test_find_by_quest_partial(
        self, repo: ItemRepository, weapon_item: DDOItem
    ) -> None:
        repo.save(weapon_item)
        names = repo.find_by_quest("Pit")
        assert weapon_item.name in names


# ── SchemaError on open failure ───────────────────────────────────────────────


class TestSchemaError:
    def test_schema_error_on_connect_failure(self) -> None:
        repo = ItemRepository(":memory:")
        with patch("sqlite3.connect", side_effect=sqlite3.Error("disk I/O error")):
            with pytest.raises(SchemaError):
                repo.open()

    def test_conn_is_none_after_schema_error(self) -> None:
        repo = ItemRepository(":memory:")
        with patch("sqlite3.connect", side_effect=sqlite3.Error("boom")):
            try:
                repo.open()
            except SchemaError:
                pass
        assert repo._conn is None


# ── delete_by_id ──────────────────────────────────────────────────────────────


class TestDeleteById:
    def test_delete_by_id_removes_item(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        item_id = repo.save(cloak_item)
        repo.delete_by_id(item_id)
        assert not repo.exists(cloak_item.name)

    def test_delete_by_id_missing_raises(self, repo: ItemRepository) -> None:
        with pytest.raises(ItemNotFoundError):
            repo.delete_by_id(9999)

    def test_delete_by_id_count_decrements(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        item_id = repo.save(cloak_item)
        assert repo.count() == 1
        repo.delete_by_id(item_id)
        assert repo.count() == 0


# ── DB-level errors (sqlite3.Error paths) ────────────────────────────────────


class TestDbErrors:
    """Each method must raise ItemDbError when the underlying sqlite3 call fails."""

    def _mock_conn(self) -> MagicMock:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.Error("simulated db error")
        mock_conn.executemany.side_effect = sqlite3.Error("simulated db error")
        return mock_conn

    def test_get_or_none_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.get_or_none("any")

    def test_get_by_id_raises_not_found(self, repo: ItemRepository) -> None:
        with pytest.raises(ItemNotFoundError):
            repo.get_by_id(9999)

    def test_get_by_id_or_none_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.get_by_id_or_none(1)

    def test_get_id_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.get_id("any")

    def test_list_names_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.list_names()

    def test_count_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.count()

    def test_exists_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.exists("any")

    def test_search_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.search(ItemFilter())

    def test_find_by_enchantment_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.find_by_enchantment("Vorpal")

    def test_find_by_set_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.find_by_set("Some Set")

    def test_find_by_quest_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.find_by_quest("Some Quest")

    def test_get_scraped_at_db_error(self, repo: ItemRepository) -> None:
        mock_conn = self._mock_conn()
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.get_scraped_at("any")

    def test_upsert_db_error(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        mock_conn = MagicMock()
        # Make conn act as a context manager but raise on execute inside write
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.side_effect = sqlite3.Error("upsert error")
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.upsert(cloak_item)

    def test_delete_db_error(self, repo: ItemRepository) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.side_effect = sqlite3.Error("delete error")
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.delete("any name")

    def test_delete_by_id_db_error(self, repo: ItemRepository) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.side_effect = sqlite3.Error("delete by id error")
        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.delete_by_id(42)

    def test_save_non_unique_integrity_error(
        self, repo: ItemRepository, cloak_item: DDOItem
    ) -> None:
        """A non-UNIQUE IntegrityError in save() raises ItemDbError, not DuplicateItemError."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with patch.object(
                _ItemWriter,
                "write",
                side_effect=sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
            ):
                with pytest.raises(ItemDbError) as exc_info:
                    repo.save(cloak_item)
                # Must NOT be DuplicateItemError
                assert not isinstance(exc_info.value, DuplicateItemError)

    def test_close_commit_error_is_swallowed(self, repo: ItemRepository) -> None:
        """sqlite3.Error raised during commit() in close() is silently swallowed."""
        # Ensure the repo has an open connection first.
        repo.open()
        mock_conn = MagicMock()
        mock_conn.commit.side_effect = sqlite3.Error("commit failed")
        repo._conn = mock_conn
        # Must not raise even though commit() fails.
        repo.close()
        assert repo._conn is None

    def test_save_db_error(self, repo: ItemRepository, cloak_item: DDOItem) -> None:
        """A bare sqlite3.Error (not IntegrityError) in save() raises ItemDbError."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.side_effect = sqlite3.Error("db error")
        mock_conn.executemany.side_effect = sqlite3.Error("db error")

        with patch.object(repo, "_get_conn", return_value=mock_conn):
            with pytest.raises(ItemDbError):
                repo.save(cloak_item)
