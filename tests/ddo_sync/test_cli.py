"""Tests for ddo_sync.cli."""

from __future__ import annotations

import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ddo_sync.cli import (
    _build_parser,
    _cmd_discover,
    _cmd_reset_failed,
    _cmd_status,
    _cmd_sync,
    _configure_logging,
    _install_sigint_handler,
    _print_summary,
    _run_sync,
    main,
)
from ddo_sync.exceptions import UpdatePageError
from ddo_sync.models import QueueStats, SyncStatus, UpdatePageStatus

# ── Helpers ───────────────────────────────────────────────────────────────────


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _make_queue_stats(
    pending: int = 0,
    in_progress: int = 0,
    complete: int = 5,
    failed: int = 0,
    skipped: int = 0,
) -> QueueStats:
    return QueueStats(
        pending=pending,
        in_progress=in_progress,
        complete=complete,
        failed=failed,
        skipped=skipped,
    )


def _make_update_page_status(
    page_name: str = "Update_5_named_items",
    needs_resync_val: bool = False,
) -> UpdatePageStatus:
    # needs_resync is a computed property; control it via timestamps
    if needs_resync_val:
        # wiki newer → needs resync
        last_synced_at = utc(2025, 11, 1)
        wiki_modified_at = utc(2025, 11, 2)
    else:
        last_synced_at = utc(2025, 11, 2)
        wiki_modified_at = utc(2025, 11, 1)
    return UpdatePageStatus(
        page_name=page_name,
        page_url=f"https://ddowiki.com/page/{page_name}",
        last_synced_at=last_synced_at,
        wiki_modified_at=wiki_modified_at,
    )


def _make_sync_status(failed: int = 0, stale_pages: bool = False) -> SyncStatus:
    stats = _make_queue_stats(failed=failed)
    page = _make_update_page_status(needs_resync_val=stale_pages)
    return SyncStatus(
        queue_stats=stats,
        update_pages={"Update_5_named_items": page},
    )


# ── Argument parsing (_build_parser) ─────────────────────────────────────────


class TestBuildParser:
    def test_defaults(self):

        args = _build_parser().parse_args([])
        assert args.status is False
        assert args.discover is False
        assert args.reset_failed is False
        assert args.pages is None
        assert args.limit is None
        assert args.rate_limit == 2.5
        assert args.item is None
        assert args.item_override is False
        assert args.max_retries == 3
        assert args.verbose is False

    def test_status_flag(self):

        args = _build_parser().parse_args(["--status"])
        assert args.status is True

    def test_discover_flag(self):

        args = _build_parser().parse_args(["--discover"])
        assert args.discover is True

    def test_reset_failed_flag(self):

        args = _build_parser().parse_args(["--reset-failed"])
        assert args.reset_failed is True

    def test_page_single(self):

        args = _build_parser().parse_args(["--page", "Update_5_named_items"])
        assert args.pages == ["Update_5_named_items"]

    def test_page_multiple(self):

        args = _build_parser().parse_args(
            ["--page", "Update_5_named_items", "Update_6_named_items"]
        )
        assert args.pages == ["Update_5_named_items", "Update_6_named_items"]

    def test_limit(self):

        args = _build_parser().parse_args(["--limit", "42"])
        assert args.limit == 42

    def test_rate_limit(self):

        args = _build_parser().parse_args(["--rate-limit", "5.0"])
        assert args.rate_limit == 5.0

    def test_item(self):

        args = _build_parser().parse_args(["--item", "Lenses of Opportunity"])
        assert args.item == "Lenses of Opportunity"

    def test_item_override(self):

        args = _build_parser().parse_args(["--item", "Sword", "--item-override"])
        assert args.item_override is True

    def test_max_retries(self):

        args = _build_parser().parse_args(["--max-retries", "5"])
        assert args.max_retries == 5

    def test_verbose(self):

        args = _build_parser().parse_args(["--verbose"])
        assert args.verbose is True

    def test_mutually_exclusive_status_discover(self):

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--status", "--discover"])

    def test_mutually_exclusive_status_reset(self):

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--status", "--reset-failed"])


# ── _configure_logging ────────────────────────────────────────────────────────


class TestConfigureLogging:
    def test_verbose_true(self):

        # Should not raise
        _configure_logging(verbose=True)

    def test_verbose_false(self):

        _configure_logging(verbose=False)


# ── _print_summary ────────────────────────────────────────────────────────────


class TestPrintSummary:
    def test_no_stale_pages(self):

        status = _make_sync_status(failed=0, stale_pages=False)
        # Should not raise
        _print_summary(status)

    def test_with_stale_pages(self):

        status = _make_sync_status(failed=2, stale_pages=True)
        _print_summary(status)

    def test_with_failed_items(self):

        status = _make_sync_status(failed=3, stale_pages=False)
        _print_summary(status)

    def test_empty_update_pages(self):

        stats = _make_queue_stats(complete=0)
        status = SyncStatus(queue_stats=stats, update_pages={})
        _print_summary(status)


# ── _install_sigint_handler ───────────────────────────────────────────────────


class TestInstallSigintHandler:
    def test_installs_handler(self):

        _install_sigint_handler()
        handler = signal.getsignal(signal.SIGINT)
        # The handler should now be a custom callable (not SIG_DFL or default)
        assert callable(handler)
        assert handler is not signal.SIG_DFL

    def test_handler_raises_keyboard_interrupt(self):

        _install_sigint_handler()
        handler = signal.getsignal(signal.SIGINT)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)


# ── _cmd_status ───────────────────────────────────────────────────────────────


class TestCmdStatus:
    def test_no_db_returns_zero(self):

        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = False

        with patch("ddo_sync.cli.QUEUE_DB", fake_path):
            result = _cmd_status()

        assert result == 0

    def test_with_db_returns_zero(self):

        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = True
        fake_path.__str__ = lambda _: "/fake/queue.db"

        stats = _make_queue_stats(pending=2, complete=10, failed=1)
        page_no_resync = _make_update_page_status(needs_resync_val=False)
        page_with_resync = _make_update_page_status(
            page_name="Update_6_named_items", needs_resync_val=True
        )

        mock_qr = MagicMock()
        mock_qr.__enter__ = MagicMock(return_value=mock_qr)
        mock_qr.__exit__ = MagicMock(return_value=None)
        mock_qr.get_queue_stats.return_value = stats
        mock_qr.list_update_pages.return_value = [page_no_resync, page_with_resync]

        with (
            patch("ddo_sync.cli.QUEUE_DB", fake_path),
            patch("ddo_sync.cli.QueueRepository", return_value=mock_qr),
        ):
            result = _cmd_status()

        assert result == 0
        mock_qr.get_queue_stats.assert_called_once()
        mock_qr.list_update_pages.assert_called_once()

    def test_with_db_page_never_synced(self):
        """UpdatePageStatus with last_synced_at=None shows 'never'."""

        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = True
        fake_path.__str__ = lambda _: "/fake/queue.db"

        stats = _make_queue_stats()
        never_synced_page = UpdatePageStatus(
            page_name="Update_7_named_items",
            page_url="https://ddowiki.com/page/Update_7_named_items",
            last_synced_at=None,
            wiki_modified_at=None,
        )

        mock_qr = MagicMock()
        mock_qr.__enter__ = MagicMock(return_value=mock_qr)
        mock_qr.__exit__ = MagicMock(return_value=None)
        mock_qr.get_queue_stats.return_value = stats
        mock_qr.list_update_pages.return_value = [never_synced_page]

        with (
            patch("ddo_sync.cli.QUEUE_DB", fake_path),
            patch("ddo_sync.cli.QueueRepository", return_value=mock_qr),
        ):
            result = _cmd_status()

        assert result == 0


# ── _cmd_discover ─────────────────────────────────────────────────────────────


class TestCmdDiscover:
    def test_success_returns_zero(self):

        mock_discoverer = MagicMock()
        mock_discoverer.discover.return_value = [
            "Update_5_named_items",
            "Update_6_named_items",
        ]
        mock_cls = MagicMock(return_value=mock_discoverer)

        with patch("ddo_sync.cli.UpdatePageDiscoverer", mock_cls):
            result = _cmd_discover()

        assert result == 0
        mock_discoverer.discover.assert_called_once()

    def test_empty_pages_returns_zero(self):

        mock_discoverer = MagicMock()
        mock_discoverer.discover.return_value = []
        mock_cls = MagicMock(return_value=mock_discoverer)

        with patch("ddo_sync.cli.UpdatePageDiscoverer", mock_cls):
            result = _cmd_discover()

        assert result == 0

    def test_failure_returns_one(self):

        mock_discoverer = MagicMock()
        mock_discoverer.discover.side_effect = RuntimeError("network error")
        mock_cls = MagicMock(return_value=mock_discoverer)

        with patch("ddo_sync.cli.UpdatePageDiscoverer", mock_cls):
            result = _cmd_discover()

        assert result == 1


# ── _cmd_reset_failed ─────────────────────────────────────────────────────────


class TestCmdResetFailed:
    def test_no_db_returns_zero(self):

        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = False

        with patch("ddo_sync.cli.QUEUE_DB", fake_path):
            result = _cmd_reset_failed()

        assert result == 0

    def test_with_db_resets_and_returns_zero(self):

        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = True
        fake_path.__str__ = lambda _: "/fake/queue.db"

        mock_qr = MagicMock()
        mock_qr.__enter__ = MagicMock(return_value=mock_qr)
        mock_qr.__exit__ = MagicMock(return_value=None)
        mock_qr.reset_failed_to_pending.return_value = 3

        with (
            patch("ddo_sync.cli.QUEUE_DB", fake_path),
            patch("ddo_sync.cli.QueueRepository", return_value=mock_qr),
        ):
            result = _cmd_reset_failed()

        assert result == 0
        mock_qr.reset_failed_to_pending.assert_called_once_with(max_retries=9999)

    def test_with_db_zero_resets(self):

        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = True
        fake_path.__str__ = lambda _: "/fake/queue.db"

        mock_qr = MagicMock()
        mock_qr.__enter__ = MagicMock(return_value=mock_qr)
        mock_qr.__exit__ = MagicMock(return_value=None)
        mock_qr.reset_failed_to_pending.return_value = 0

        with (
            patch("ddo_sync.cli.QUEUE_DB", fake_path),
            patch("ddo_sync.cli.QueueRepository", return_value=mock_qr),
        ):
            result = _cmd_reset_failed()

        assert result == 0


# ── _cmd_sync ─────────────────────────────────────────────────────────────────


class TestCmdSync:
    def _make_mocks(self):
        """Create a full set of mocks for _cmd_sync."""
        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.__enter__ = MagicMock(return_value=mock_fetcher_instance)
        mock_fetcher_instance.__exit__ = MagicMock(return_value=None)

        mock_item_repo_instance = MagicMock()
        mock_item_repo_instance.__enter__ = MagicMock(
            return_value=mock_item_repo_instance
        )
        mock_item_repo_instance.__exit__ = MagicMock(return_value=None)

        mock_queue_repo_instance = MagicMock()
        mock_queue_repo_instance.__enter__ = MagicMock(
            return_value=mock_queue_repo_instance
        )
        mock_queue_repo_instance.__exit__ = MagicMock(return_value=None)
        # _run_sync accesses _queue_repo directly on syncer
        mock_queue_repo_instance.reset_failed_to_pending.return_value = 0
        mock_queue_repo_instance.list_update_pages.return_value = []

        mock_syncer = MagicMock()
        mock_syncer._queue_repo = mock_queue_repo_instance
        mock_syncer._max_retries = 3
        mock_syncer.get_status.return_value = _make_sync_status(failed=0)

        return (
            mock_fetcher_instance,
            mock_item_repo_instance,
            mock_queue_repo_instance,
            mock_syncer,
        )

    def test_with_page_names_returns_zero(self):

        (
            mock_fetcher_instance,
            mock_item_repo_instance,
            mock_queue_repo_instance,
            mock_syncer,
        ) = self._make_mocks()

        fake_data_dir = MagicMock(spec=Path)

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.WikiFetcher", return_value=mock_fetcher_instance),
            patch("ddo_sync.cli.ItemRepository", return_value=mock_item_repo_instance),
            patch(
                "ddo_sync.cli.QueueRepository", return_value=mock_queue_repo_instance
            ),
            patch("ddo_sync.cli.DDOSyncer", return_value=mock_syncer),
            patch("ddo_sync.cli.ItemNormalizer"),
            patch("ddo_sync.cli._install_sigint_handler"),
        ):
            result = _cmd_sync(
                page_names=["Update_5_named_items"],
                limit=None,
                rate_limit=2.5,
                max_retries=3,
            )

        assert result == 0
        mock_syncer.register_update_page.assert_called_once_with("Update_5_named_items")

    def test_page_names_with_spaces_normalized(self):

        (
            mock_fetcher_instance,
            mock_item_repo_instance,
            mock_queue_repo_instance,
            mock_syncer,
        ) = self._make_mocks()

        fake_data_dir = MagicMock(spec=Path)

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.WikiFetcher", return_value=mock_fetcher_instance),
            patch("ddo_sync.cli.ItemRepository", return_value=mock_item_repo_instance),
            patch(
                "ddo_sync.cli.QueueRepository", return_value=mock_queue_repo_instance
            ),
            patch("ddo_sync.cli.DDOSyncer", return_value=mock_syncer),
            patch("ddo_sync.cli.ItemNormalizer"),
            patch("ddo_sync.cli._install_sigint_handler"),
        ):
            result = _cmd_sync(
                page_names=["Update 5 named items"],
                limit=10,
                rate_limit=1.0,
                max_retries=3,
            )

        assert result == 0
        mock_syncer.register_update_page.assert_called_once_with("Update_5_named_items")

    def test_auto_discover_success(self):

        (
            mock_fetcher_instance,
            mock_item_repo_instance,
            mock_queue_repo_instance,
            mock_syncer,
        ) = self._make_mocks()

        mock_discoverer = MagicMock()
        mock_discoverer.discover.return_value = ["Update_5_named_items"]
        mock_discoverer_cls = MagicMock(return_value=mock_discoverer)

        fake_data_dir = MagicMock(spec=Path)

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.UpdatePageDiscoverer", mock_discoverer_cls),
            patch("ddo_sync.cli.WikiFetcher", return_value=mock_fetcher_instance),
            patch("ddo_sync.cli.ItemRepository", return_value=mock_item_repo_instance),
            patch(
                "ddo_sync.cli.QueueRepository", return_value=mock_queue_repo_instance
            ),
            patch("ddo_sync.cli.DDOSyncer", return_value=mock_syncer),
            patch("ddo_sync.cli.ItemNormalizer"),
            patch("ddo_sync.cli._install_sigint_handler"),
        ):
            result = _cmd_sync(
                page_names=None,
                limit=None,
                rate_limit=2.5,
                max_retries=3,
            )

        assert result == 0

    def test_auto_discover_failure_returns_one(self):

        mock_discoverer = MagicMock()
        mock_discoverer.discover.side_effect = RuntimeError("timeout")
        mock_discoverer_cls = MagicMock(return_value=mock_discoverer)

        fake_data_dir = MagicMock(spec=Path)

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.UpdatePageDiscoverer", mock_discoverer_cls),
        ):
            result = _cmd_sync(
                page_names=None,
                limit=None,
                rate_limit=2.5,
                max_retries=3,
            )

        assert result == 1

    def test_auto_discover_empty_returns_zero(self):

        mock_discoverer = MagicMock()
        mock_discoverer.discover.return_value = []
        mock_discoverer_cls = MagicMock(return_value=mock_discoverer)

        fake_data_dir = MagicMock(spec=Path)

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.UpdatePageDiscoverer", mock_discoverer_cls),
        ):
            result = _cmd_sync(
                page_names=None,
                limit=None,
                rate_limit=2.5,
                max_retries=3,
            )

        assert result == 0

    def test_keyboard_interrupt_returns_one(self):

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.__enter__ = MagicMock(side_effect=KeyboardInterrupt)
        mock_fetcher_instance.__exit__ = MagicMock(return_value=None)

        fake_data_dir = MagicMock(spec=Path)

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.WikiFetcher", return_value=mock_fetcher_instance),
            patch("ddo_sync.cli._install_sigint_handler"),
        ):
            result = _cmd_sync(
                page_names=["Update_5_named_items"],
                limit=None,
                rate_limit=2.5,
                max_retries=3,
            )

        assert result == 1

    def test_fatal_exception_returns_one(self):

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.__enter__ = MagicMock(
            side_effect=RuntimeError("db locked")
        )
        mock_fetcher_instance.__exit__ = MagicMock(return_value=None)

        fake_data_dir = MagicMock(spec=Path)

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.WikiFetcher", return_value=mock_fetcher_instance),
            patch("ddo_sync.cli._install_sigint_handler"),
        ):
            result = _cmd_sync(
                page_names=["Update_5_named_items"],
                limit=None,
                rate_limit=2.5,
                max_retries=3,
            )

        assert result == 1

    def test_failed_items_returns_two(self):

        (
            mock_fetcher_instance,
            mock_item_repo_instance,
            mock_queue_repo_instance,
            mock_syncer,
        ) = self._make_mocks()
        # Override syncer to report failures
        mock_syncer.get_status.return_value = _make_sync_status(failed=2)

        fake_data_dir = MagicMock(spec=Path)

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.WikiFetcher", return_value=mock_fetcher_instance),
            patch("ddo_sync.cli.ItemRepository", return_value=mock_item_repo_instance),
            patch(
                "ddo_sync.cli.QueueRepository", return_value=mock_queue_repo_instance
            ),
            patch("ddo_sync.cli.DDOSyncer", return_value=mock_syncer),
            patch("ddo_sync.cli.ItemNormalizer"),
            patch("ddo_sync.cli._install_sigint_handler"),
        ):
            result = _cmd_sync(
                page_names=["Update_5_named_items"],
                limit=None,
                rate_limit=2.5,
                max_retries=3,
            )

        assert result == 2

    def test_rate_limit_minimum_enforced(self):
        """Rate limit below 1.0 is clamped to 1.0."""

        (
            mock_fetcher_instance,
            mock_item_repo_instance,
            mock_queue_repo_instance,
            mock_syncer,
        ) = self._make_mocks()

        fake_data_dir = MagicMock(spec=Path)
        captured_configs = []

        def capture_config(cfg):
            captured_configs.append(cfg)
            return mock_fetcher_instance

        with (
            patch("ddo_sync.cli.DATA_DIR", fake_data_dir),
            patch("ddo_sync.cli.WikiFetcher", side_effect=capture_config),
            patch("ddo_sync.cli.ItemRepository", return_value=mock_item_repo_instance),
            patch(
                "ddo_sync.cli.QueueRepository", return_value=mock_queue_repo_instance
            ),
            patch("ddo_sync.cli.DDOSyncer", return_value=mock_syncer),
            patch("ddo_sync.cli.ItemNormalizer"),
            patch("ddo_sync.cli._install_sigint_handler"),
        ):
            _cmd_sync(
                page_names=["Update_5_named_items"],
                limit=None,
                rate_limit=0.1,  # below minimum
                max_retries=3,
            )

        assert len(captured_configs) == 1
        assert captured_configs[0].rate_limit_delay == 1.0


# ── _run_sync ─────────────────────────────────────────────────────────────────


class TestRunSync:
    def test_resets_and_processes_queue(self):

        mock_queue_repo = MagicMock()
        mock_queue_repo.reset_failed_to_pending.return_value = 0
        mock_queue_repo.list_update_pages.return_value = []

        mock_syncer = MagicMock()
        mock_syncer._queue_repo = mock_queue_repo
        mock_syncer._max_retries = 3
        mock_syncer.get_status.return_value = _make_sync_status()

        result = _run_sync(mock_syncer, limit=None)

        assert isinstance(result, SyncStatus)
        mock_queue_repo.reset_failed_to_pending.assert_called_once_with(3)
        mock_syncer.process_queue.assert_called_once_with(limit=None)

    def test_logs_reset_count_when_nonzero(self):

        mock_queue_repo = MagicMock()
        mock_queue_repo.reset_failed_to_pending.return_value = 5
        mock_queue_repo.list_update_pages.return_value = []

        mock_syncer = MagicMock()
        mock_syncer._queue_repo = mock_queue_repo
        mock_syncer._max_retries = 3
        mock_syncer.get_status.return_value = _make_sync_status()

        _run_sync(mock_syncer, limit=10)
        mock_syncer.process_queue.assert_called_once_with(limit=10)

    def test_refreshes_timestamps_for_each_page(self):

        page_status = _make_update_page_status(needs_resync_val=False)
        updated_status = _make_update_page_status(needs_resync_val=False)

        mock_queue_repo = MagicMock()
        mock_queue_repo.reset_failed_to_pending.return_value = 0
        mock_queue_repo.list_update_pages.return_value = [page_status]
        mock_queue_repo.get_update_page_status.return_value = updated_status

        mock_syncer = MagicMock()
        mock_syncer._queue_repo = mock_queue_repo
        mock_syncer._max_retries = 3
        mock_syncer.get_status.return_value = _make_sync_status()

        _run_sync(mock_syncer, limit=None)

        mock_syncer._refresh_wiki_timestamp.assert_called_once_with(
            page_status.page_name
        )

    def test_syncs_stale_pages(self):

        page_status = _make_update_page_status(needs_resync_val=True)
        updated_status = _make_update_page_status(needs_resync_val=True)

        mock_queue_repo = MagicMock()
        mock_queue_repo.reset_failed_to_pending.return_value = 0
        mock_queue_repo.list_update_pages.return_value = [page_status]
        mock_queue_repo.get_update_page_status.return_value = updated_status

        mock_syncer = MagicMock()
        mock_syncer._queue_repo = mock_queue_repo
        mock_syncer._max_retries = 3
        mock_syncer.get_status.return_value = _make_sync_status()

        _run_sync(mock_syncer, limit=None)

        mock_syncer.sync_update_page.assert_called_once_with(page_status.page_name)

    def test_handles_update_page_error(self):

        page_status = _make_update_page_status(needs_resync_val=True)
        updated_status = _make_update_page_status(needs_resync_val=True)

        mock_queue_repo = MagicMock()
        mock_queue_repo.reset_failed_to_pending.return_value = 0
        mock_queue_repo.list_update_pages.return_value = [page_status]
        mock_queue_repo.get_update_page_status.return_value = updated_status

        mock_syncer = MagicMock()
        mock_syncer._queue_repo = mock_queue_repo
        mock_syncer._max_retries = 3
        mock_syncer.sync_update_page.side_effect = UpdatePageError("fetch failed")
        mock_syncer.get_status.return_value = _make_sync_status()

        # Should not raise
        result = _run_sync(mock_syncer, limit=None)
        assert isinstance(result, SyncStatus)

    def test_skips_sync_when_get_update_page_status_returns_none(self):

        page_status = _make_update_page_status(needs_resync_val=True)

        mock_queue_repo = MagicMock()
        mock_queue_repo.reset_failed_to_pending.return_value = 0
        mock_queue_repo.list_update_pages.return_value = [page_status]
        mock_queue_repo.get_update_page_status.return_value = None

        mock_syncer = MagicMock()
        mock_syncer._queue_repo = mock_queue_repo
        mock_syncer._max_retries = 3
        mock_syncer.get_status.return_value = _make_sync_status()

        _run_sync(mock_syncer, limit=None)

        # sync_update_page should NOT be called when status is None
        mock_syncer.sync_update_page.assert_not_called()


# ── main() dispatch ───────────────────────────────────────────────────────────


class TestMain:
    def test_main_status_mode(self, monkeypatch):

        monkeypatch.setattr(sys, "argv", ["ddoloot", "--status"])

        with patch("ddo_sync.cli._cmd_status", return_value=0) as mock_cmd:
            result = main()

        assert result == 0
        mock_cmd.assert_called_once()

    def test_main_discover_mode(self, monkeypatch):

        monkeypatch.setattr(sys, "argv", ["ddoloot", "--discover"])

        with patch("ddo_sync.cli._cmd_discover", return_value=0) as mock_cmd:
            result = main()

        assert result == 0
        mock_cmd.assert_called_once()

    def test_main_reset_failed_mode(self, monkeypatch):

        monkeypatch.setattr(sys, "argv", ["ddoloot", "--reset-failed"])

        with patch("ddo_sync.cli._cmd_reset_failed", return_value=0) as mock_cmd:
            result = main()

        assert result == 0
        mock_cmd.assert_called_once()

    def test_main_item_mode(self, monkeypatch):

        monkeypatch.setattr(sys, "argv", ["ddoloot", "--item", "Lenses of Opportunity"])

        with patch("ddo_sync.cli.normalize_item") as mock_normalize:
            result = main()

        assert result == 0
        mock_normalize.assert_called_once()
        call_kwargs = mock_normalize.call_args
        assert call_kwargs[0][0] == "Lenses of Opportunity"

    def test_main_item_mode_with_override(self, monkeypatch):

        monkeypatch.setattr(
            sys, "argv", ["ddoloot", "--item", "Sword", "--item-override"]
        )

        with patch("ddo_sync.cli.normalize_item") as mock_normalize:
            result = main()

        assert result == 0
        call_kwargs = mock_normalize.call_args
        assert call_kwargs[1].get("upsert") is True or call_kwargs[0][1] is True

    def test_main_default_sync(self, monkeypatch):

        monkeypatch.setattr(sys, "argv", ["ddoloot"])

        with patch("ddo_sync.cli._cmd_sync", return_value=0) as mock_cmd:
            result = main()

        assert result == 0
        mock_cmd.assert_called_once()

    def test_main_sync_with_pages(self, monkeypatch):

        monkeypatch.setattr(
            sys,
            "argv",
            ["ddoloot", "--page", "Update_5_named_items", "--limit", "10"],
        )

        with patch("ddo_sync.cli._cmd_sync", return_value=0) as mock_cmd:
            result = main()

        assert result == 0
        mock_cmd.assert_called_once_with(
            page_names=["Update_5_named_items"],
            limit=10,
            rate_limit=2.5,
            max_retries=3,
        )

    def test_main_verbose_mode(self, monkeypatch):

        monkeypatch.setattr(sys, "argv", ["ddoloot", "--verbose", "--status"])

        with patch("ddo_sync.cli._cmd_status", return_value=0):
            with patch("ddo_sync.cli._configure_logging") as mock_log:
                result = main()

        mock_log.assert_called_once_with(True)
        assert result == 0
