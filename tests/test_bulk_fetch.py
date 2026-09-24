"""Offline tests for ``scripts/bulk_fetch.py``: page choice, the budget stop, the exit-code
handling, the pause and ``--dry``. Nothing here reaches the wiki: the queue is a temporary
DB, the Page Store a temporary directory filled by a canned transport, and the guarded
run and the sleep are fakes."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ddo_sync.discovery import update_page_url
from ddo_sync.models import ItemLink
from ddo_sync.queue_db import QueueRepository
from page_store import PageStore, load_scraper_config
from tests.canned import CannedTransport, ok

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bulk_fetch.py"
_spec = importlib.util.spec_from_file_location("bulk_fetch", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
sys.modules["bulk_fetch"] = bf
_spec.loader.exec_module(bf)

ITEM = "https://ddowiki.com/page/Item:"
U8, U9, U10, U14 = (f"Update_{n}_named_items" for n in (8, 9, 10, 14))
SEED = [f"Update_{n}_named_items" for n in (5, 8, 9, 10, 14, 15)]


def get(url: str) -> str:
    return f"2026-09-24 09:41:09 | DEBUG    | GET {url} (browser)"


# ── Queue and Page Store fixtures ────────────────────────────────────────────


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "scraper.yaml"
    path.write_text(
        'user_agent: "test"\nmax_retries: 0\nrespect_robots_txt: false\n'
        f"cache_dir: {tmp_path / 'pages'}\n"
        "browser:\n  enabled: true\n  consecutive_challenges: 1\n",
        encoding="utf-8",
    )
    return path


def _hold(config_path: Path, *urls: str) -> None:
    plain = CannedTransport(default=lambda _u: ok("<html>held</html>"))
    config = load_scraper_config(config_path)
    with PageStore(config, transport=plain, sleep=lambda _s: None) as store:
        for url in urls:
            store.get(url)


def _queue(path: Path, rows: dict[str, list[str]]) -> None:
    """*rows*: update page (read) -> item names, all pending."""
    with QueueRepository(str(path)) as repo:
        for page, names in rows.items():
            repo.register_update_page(page, update_page_url(page))
            repo.mark_page_synced(page, datetime.now(timezone.utc), 1)
            repo.enqueue_items(
                [
                    ItemLink(item_name=n, wiki_url=ITEM + n, update_page=page)
                    for n in names
                ]
            )


@pytest.fixture
def setup(tmp_path):
    """Updates 5, 8, 9 and 10 read and held; 3 pending rows on 8 and 9, 1 held."""
    config = _write_config(tmp_path)
    queue = tmp_path / "queue.db"
    _queue(
        queue,
        {
            "Update_5_named_items": ["Z"],
            U10: ["X"],
            U9: ["C"],
            U8: ["A", "B"],
        },
    )
    with QueueRepository(str(queue)) as repo:
        for item in repo.get_pending_items():
            if item.item_name in ("Z", "X"):
                repo.mark_complete(item.id, datetime.now(timezone.utc))
    _hold(config, *(update_page_url(p) for p in ("Update_5_named_items", U8, U9, U10)))
    _hold(config, ITEM + "B")
    return config, queue


class FakeRun:
    """Stands in for guarded_sync's run: writes the log lines, returns the exit code and,
    with *progress*, completes the first pending row of the ``--queue-db`` it is given.
    """

    def __init__(self, *results: tuple[int, int], progress: bool = True) -> None:
        self.results = list(results)  # (exit code, GET lines) per run
        self.progress = progress
        self.calls: list[list[str]] = []

    def __call__(self, command, log_path: Path):
        self.calls.append(list(command))
        code, gets = self.results.pop(0) if self.results else (0, 0)
        if self.progress:
            queue = command[command.index("--queue-db") + 1]
            with QueueRepository(queue) as repo:
                for item in repo.get_pending_items(limit=1):
                    repo.mark_complete(item.id, datetime.now(timezone.utc))
        lines = [get(f"{ITEM}{i}") for i in range(gets)]
        lines.append("2026-09-24 09:41:10 | DEBUG    | Completed: 'A'")
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return code, f"sync exited {code}"


def _loop(tmp_path, config, queue, *args, run=None, sleeps=None, seed=SEED):
    opts = bf.parse_args(
        [
            "--log-dir",
            str(tmp_path / "logs"),
            "--scraper-config",
            str(config),
            "--queue-db",
            str(queue),
            *args,
        ]
    )
    opts.log_dir.mkdir(exist_ok=True)
    seams = bf.Seams(
        read_seed=lambda: list(seed),
        run=run or FakeRun(),
        sleep=(sleeps.append if sleeps is not None else _no_sleep),
    )
    return bf.run_loop(opts, seams)


def _no_sleep(_s):
    raise AssertionError("no pause expected")


# ── Page choice ──────────────────────────────────────────────────────────────


def test_pages_are_the_pending_ones_then_the_next_unread_in_numeric_order():
    view = bf.QueueView(pending={U10: 3, U9: 2}, read={U9, U10, "Update_5_named_items"})
    assert bf.choose_pages(SEED, view, limit=100) == [U9, U10, U8]


def test_no_unread_page_is_added_when_the_pending_rows_fill_the_run():
    view = bf.QueueView(pending={U8: 60, U9: 40}, read={U8, U9})
    assert bf.choose_pages(SEED, view, limit=100) == [U8, U9]
    assert bf.choose_pages(SEED, view, limit=101) == [U8, U9, "Update_5_named_items"]


def test_a_registered_page_not_yet_read_counts_as_unread():
    view = bf.QueueView(pending={}, read={"Update_5_named_items", U8, U9})
    assert bf.choose_pages(SEED, view, limit=100) == [U10]


def test_the_backlog_is_complete_when_nothing_is_pending_or_unread():
    view = bf.QueueView(pending={}, read=set(SEED))
    assert bf.choose_pages(SEED, view, limit=100) == []


def test_the_queue_is_read_without_writing_it(setup):
    _config, queue = setup
    before = queue.read_bytes()
    view = bf.read_queue(queue)
    assert view.pending == {U8: 2, U9: 1}
    assert view.read == {"Update_5_named_items", U8, U9, U10}
    assert queue.read_bytes() == before


def test_backlog_complete_stops_without_a_run(setup, tmp_path, capsys):
    config, queue = setup
    run = FakeRun()
    seed = ["Update_5_named_items", U10]
    with QueueRepository(str(queue)) as repo:
        for item in repo.get_pending_items():
            repo.mark_complete(item.id, datetime.now(timezone.utc))
    assert _loop(tmp_path, config, queue, run=run, seed=seed) == 0
    assert run.calls == []
    assert "stopped: backlog complete" in capsys.readouterr().out


# ── Budget ───────────────────────────────────────────────────────────────────


def test_a_worst_case_above_the_budget_stops_before_the_run(setup, tmp_path, capsys):
    config, queue = setup
    run = FakeRun()
    # pending A, B (held), C and the unread Update_14 -> F <= 1 + 100, worst case 204
    assert _loop(tmp_path, config, queue, "--budget", "203", run=run) == bf.STOPPED
    assert run.calls == []
    out = capsys.readouterr().out
    assert "worst case 204 requests exceeds the 203 left in the budget" in out


def test_spent_requests_are_counted_from_the_run_logs(setup, tmp_path, capsys):
    config, queue = setup
    run, sleeps = FakeRun((2, 5)), []
    # run 1: worst case 6 (A and C unheld; 3 pending rows fill --limit 3). It completes
    # A, so run 2 adds the unread Update_14: F <= 1 + 3, worst case 10 > 12 - 5.
    code = _loop(
        tmp_path,
        config,
        queue,
        "--limit",
        "3",
        "--budget",
        "12",
        run=run,
        sleeps=sleeps,
    )
    assert code == bf.STOPPED
    assert len(run.calls) == 1
    out = capsys.readouterr().out
    assert "requests 5; completed 1 (skipped 0), failed 0; exit 2" in out
    assert "worst case 10 requests exceeds the 7 left in the budget" in out
    assert "runs: 1; requests: 5 of the 12 budget" in out
    summary = (tmp_path / "logs" / "summary.log").read_text(encoding="utf-8")
    assert summary.startswith(f"run 1: pages {U8} {U9}; requests 5;")
    assert sleeps == []  # the stop came before any pause


def test_the_run_gets_the_bulk_flags(setup, tmp_path):
    config, queue = setup
    run = FakeRun()
    _loop(tmp_path, config, queue, "--max-runs", "1", run=run)
    assert run.calls[0][-12:] == [
        "--scraper-config",
        str(config),
        "--queue-db",
        str(queue),
        "--page",
        U8,
        U9,
        U14,
        "--limit",
        "100",
        "--max-retries",
        "0",
    ]


# ── Exit codes and pauses ────────────────────────────────────────────────────


@pytest.mark.parametrize("code", [1, 3])
def test_a_stopped_run_stops_the_loop_with_the_step_6_advice(
    setup, tmp_path, capsys, code
):
    config, queue = setup
    run = FakeRun((code, 4))
    assert _loop(tmp_path, config, queue, run=run) == bf.STOPPED
    assert len(run.calls) == 1
    out = capsys.readouterr().out
    who = {1: "the sync stopped (exit 1", 3: "the guard stopped the sync (exit 3)"}
    assert f"stopped: {who[code]}" in out
    assert "Stop for the day" in out
    assert "403 or 405 means stop until the maintainer decides" in out
    assert "scripts/offline_rerun.py Update_5_named_items" in out


@pytest.mark.parametrize("code", [64, -9])
def test_an_unexpected_exit_code_stops_the_loop(setup, tmp_path, capsys, code):
    config, queue = setup
    run = FakeRun((code, 0))
    assert _loop(tmp_path, config, queue, run=run) == bf.STOPPED
    assert len(run.calls) == 1
    assert f"unexpected exit code {code}" in capsys.readouterr().out


def test_failed_items_remaining_continue_after_a_pause(setup, tmp_path, capsys):
    config, queue = setup
    run, sleeps = FakeRun((2, 3), (2, 3)), []
    code = _loop(
        tmp_path,
        config,
        queue,
        "--max-runs",
        "2",
        "--limit",
        "1",
        "--pause",
        "7",
        run=run,
        sleeps=sleeps,
    )
    assert code == 0
    assert len(run.calls) == 2
    assert sleeps == [7]  # between the runs, not after the last
    assert "--max-runs 2 reached" in capsys.readouterr().out
    assert sorted(p.name for p in (tmp_path / "logs").glob("run-*.log")) == [
        "run-001.log",
        "run-002.log",
    ]


def test_a_run_with_no_wiki_request_needs_no_pause(setup, tmp_path):
    config, queue = setup
    run = FakeRun((0, 0), (0, 0))
    args = ("--max-runs", "2", "--limit", "1")
    assert _loop(tmp_path, config, queue, *args, run=run) == 0
    assert len(run.calls) == 2  # _no_sleep would have raised


def test_a_run_that_changes_nothing_in_the_queue_stops_the_loop(
    setup, tmp_path, capsys
):
    config, queue = setup
    run = FakeRun((0, 0), (0, 0), progress=False)
    assert _loop(tmp_path, config, queue, "--limit", "1", run=run) == bf.STOPPED
    assert len(run.calls) == 1
    assert "the last run changed nothing in the queue" in capsys.readouterr().out


def test_an_unread_page_still_unread_after_its_run_stops_the_loop(
    setup, tmp_path, capsys
):
    config, queue = setup
    run = FakeRun((0, 3), (0, 3))  # the fake never reads Update_14 into the queue
    assert _loop(tmp_path, config, queue, run=run, sleeps=[]) == bf.STOPPED
    assert len(run.calls) == 1
    assert f"{U14} was not read into the queue" in capsys.readouterr().out


def test_ctrl_c_during_the_pause_stops_cleanly(setup, tmp_path, capsys):
    config, queue = setup

    def interrupt(_s):
        raise KeyboardInterrupt

    opts = bf.parse_args(
        [
            "--log-dir",
            str(tmp_path),
            "--scraper-config",
            str(config),
            "--queue-db",
            str(queue),
            "--limit",
            "3",
        ]
    )
    run = FakeRun((2, 1), (2, 1))
    seams = bf.Seams(read_seed=lambda: SEED, run=run, sleep=interrupt)
    assert bf.run_loop(opts, seams) == bf.INTERRUPTED
    assert len(run.calls) == 1
    assert "stopped: interrupted (Ctrl-C)" in capsys.readouterr().out


# ── Dry run ──────────────────────────────────────────────────────────────────


def test_dry_plans_the_next_run_and_sends_nothing(setup, tmp_path, capsys):
    config, queue = setup
    before = queue.read_bytes()

    def no_run(*_a):
        raise AssertionError("--dry must not run a sync")

    assert _loop(tmp_path, config, queue, "--dry", run=no_run) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"next run --page: {U8} {U9} {U14}"
    assert "pending rows processed (--limit 100): 3, unheld: 2" in out
    assert "F <= 101; worst case 1 + 2F + min(F, k) <= 204 requests" in out
    assert "budget left: 1200; fits" in out
    assert out[-1] == "stopped: dry run: no request sent"
    assert queue.read_bytes() == before
    assert not list((tmp_path / "logs").iterdir())


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_exit_codes_0_and_2_continue_and_the_rest_stop():
    assert bf.exit_stop(0) is None
    assert bf.exit_stop(2) is None
    assert "Stop for the day" in bf.exit_stop(1)
    assert "guard stopped" in bf.exit_stop(3)
    assert "unexpected" in bf.exit_stop(64)


def test_the_run_log_tally_reads_gets_and_items(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(
        "\n".join(
            [
                get(ITEM + "A"),
                "x | DEBUG | GET https://ddowiki.com/robots.txt -> 200 (plain)",
                "x | DEBUG | blocked script https://ddowiki.com/load.php (browser)",
                "x | INFO | Skipped: 'M' — not equipment: Raw ingredients",
                "x | DEBUG | Completed: 'M'",
                "x | DEBUG | Completed: 'A'",
                "x | WARNING | Failed: 'B' — FetchError: page not found (404): u",
            ]
        ),
        encoding="utf-8",
    )
    assert bf.tally_log(log) == bf.RunTally(
        requests=2, completed=2, failed=1, skipped=1
    )
