from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ddo_sync.debug_commands import normalize_item
from ddowiki_scraper.exceptions import FetchError, RobotsTxtError

FAKE_LOOT_DB = Path("/tmp/loot.db")
ITEM_NAME = "Cloak of Shadows"
FAKE_HTML = "<html>Cloak of Shadows</html>"


def _make_mock_item(
    enchantments=None,
    named_set=None,
    source=None,
):
    item = MagicMock()
    item.name = "Cloak of Shadows"
    item.slot = "Back"
    item.minimum_level = 10
    item.binding = "Bound to Character on Acquire"
    item.material = "Leather"
    item.hardness = 12
    item.durability = 80
    item.weight = 1
    item.flavor_text = "A shadowy cloak."
    item.enchantments = enchantments if enchantments is not None else []
    item.named_set = named_set
    item.source = source
    return item


def _make_enchantment(name, value=None):
    enc = MagicMock()
    enc.name = name
    enc.value = value
    return enc


def _setup_fetcher_mock(mock_fetcher_cls, side_effect=None, html=FAKE_HTML):
    mock_fetcher_instance = MagicMock()
    mock_fetcher_cls.return_value.__enter__.return_value = mock_fetcher_instance
    mock_fetcher_cls.return_value.__exit__.return_value = False
    if side_effect is not None:
        mock_fetcher_instance.fetch_item_page.side_effect = side_effect
    else:
        mock_fetcher_instance.fetch_item_page.return_value = html
    return mock_fetcher_instance


def test_robots_txt_error_returns_early(capsys):
    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls, side_effect=RobotsTxtError("blocked"))

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        captured = capsys.readouterr()
        assert "Blocked by robots.txt" in captured.out
        mock_normalizer_cls.return_value.normalize.assert_not_called()
        mock_repo_cls.assert_not_called()


def test_fetch_error_returns_early(capsys):
    exc = FetchError("not found")
    exc.status_code = 404

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls, side_effect=exc)

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        captured = capsys.readouterr()
        assert "Fetch failed" in captured.out
        assert "404" in captured.out
        mock_normalizer_cls.return_value.normalize.assert_not_called()
        mock_repo_cls.assert_not_called()


def test_successful_fetch_upsert_false_calls_save():
    mock_item = _make_mock_item()

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls)
        mock_normalizer_cls.return_value.normalize.return_value = mock_item

        mock_repo_instance = MagicMock()
        mock_repo_cls.return_value.__enter__.return_value = mock_repo_instance
        mock_repo_cls.return_value.__exit__.return_value = False

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        mock_repo_instance.save.assert_called_once_with(mock_item)
        mock_repo_instance.upsert.assert_not_called()


def test_successful_fetch_upsert_true_calls_upsert():
    mock_item = _make_mock_item()

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls)
        mock_normalizer_cls.return_value.normalize.return_value = mock_item

        mock_repo_instance = MagicMock()
        mock_repo_cls.return_value.__enter__.return_value = mock_repo_instance
        mock_repo_cls.return_value.__exit__.return_value = False

        normalize_item(ITEM_NAME, upsert=True, loot_db=FAKE_LOOT_DB)

        mock_repo_instance.upsert.assert_called_once_with(mock_item)
        mock_repo_instance.save.assert_not_called()


def test_enchantment_with_value_prints_value(capsys):
    enc = _make_enchantment("Devotion", value=90)
    mock_item = _make_mock_item(enchantments=[enc])

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls)
        mock_normalizer_cls.return_value.normalize.return_value = mock_item

        mock_repo_instance = MagicMock()
        mock_repo_cls.return_value.__enter__.return_value = mock_repo_instance
        mock_repo_cls.return_value.__exit__.return_value = False

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        captured = capsys.readouterr()
        assert "Devotion 90" in captured.out


def test_enchantment_without_value_omits_value(capsys):
    enc = _make_enchantment("Striding", value=None)
    mock_item = _make_mock_item(enchantments=[enc])

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls)
        mock_normalizer_cls.return_value.normalize.return_value = mock_item

        mock_repo_instance = MagicMock()
        mock_repo_cls.return_value.__enter__.return_value = mock_repo_instance
        mock_repo_cls.return_value.__exit__.return_value = False

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        captured = capsys.readouterr()
        assert "- Striding\n" in captured.out or "- Striding" in captured.out
        assert "- Striding None" not in captured.out


def test_named_set_present_prints_named_set(capsys):
    named_set = MagicMock()
    named_set.name = "Shadow's Embrace"
    mock_item = _make_mock_item(named_set=named_set)

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls)
        mock_normalizer_cls.return_value.normalize.return_value = mock_item

        mock_repo_instance = MagicMock()
        mock_repo_cls.return_value.__enter__.return_value = mock_repo_instance
        mock_repo_cls.return_value.__exit__.return_value = False

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        captured = capsys.readouterr()
        assert "Named set: Shadow's Embrace" in captured.out


def test_named_set_none_does_not_print_named_set(capsys):
    mock_item = _make_mock_item(named_set=None)

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls)
        mock_normalizer_cls.return_value.normalize.return_value = mock_item

        mock_repo_instance = MagicMock()
        mock_repo_cls.return_value.__enter__.return_value = mock_repo_instance
        mock_repo_cls.return_value.__exit__.return_value = False

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        captured = capsys.readouterr()
        assert "Named set" not in captured.out


def test_source_present_prints_source_quests(capsys):
    source = MagicMock()
    source.quests = ["The Pit", "Tangleroot Gorge"]
    mock_item = _make_mock_item(source=source)

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls)
        mock_normalizer_cls.return_value.normalize.return_value = mock_item

        mock_repo_instance = MagicMock()
        mock_repo_cls.return_value.__enter__.return_value = mock_repo_instance
        mock_repo_cls.return_value.__exit__.return_value = False

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        captured = capsys.readouterr()
        assert "Source quests" in captured.out


def test_source_none_does_not_print_source(capsys):
    mock_item = _make_mock_item(source=None)

    with (
        patch("ddo_sync.debug_commands.WikiFetcher") as mock_fetcher_cls,
        patch("ddo_sync.debug_commands.ItemNormalizer") as mock_normalizer_cls,
        patch("ddo_sync.debug_commands.ItemRepository") as mock_repo_cls,
    ):

        _setup_fetcher_mock(mock_fetcher_cls)
        mock_normalizer_cls.return_value.normalize.return_value = mock_item

        mock_repo_instance = MagicMock()
        mock_repo_cls.return_value.__enter__.return_value = mock_repo_instance
        mock_repo_cls.return_value.__exit__.return_value = False

        normalize_item(ITEM_NAME, upsert=False, loot_db=FAKE_LOOT_DB)

        captured = capsys.readouterr()
        assert "Source quests" not in captured.out
