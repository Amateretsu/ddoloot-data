"""Offline tests for ``scripts/guarded_sync.py``: the stop rules, the worst case, the
dry run and the stop of a fake sync. Nothing here reaches the wiki; the "sync" child is a
``python -c`` that prints log lines."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from loguru import logger

from ddo_sync.discovery import update_page_url
from ddo_sync.models import ItemLink
from ddo_sync.queue_db import QueueRepository
from page_store import PageStore, Response, load_scraper_config
from tests.canned import (
    CHALLENGE,
    CannedTransport,
    FakeBrowserPage,
    fake_playwright_module,
    ok,
)

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guarded_sync.py"
_spec = importlib.util.spec_from_file_location("guarded_sync", _SCRIPT)
gs = importlib.util.module_from_spec(_spec)
sys.modules["guarded_sync"] = gs
_spec.loader.exec_module(gs)

FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}"
ITEM = "https://ddowiki.com/page/Item:"


def line(level: str, msg: str) -> str:
    return f"2026-09-24 09:41:09 | {level:<8} | {msg}"


def throttled(status: int = 503, url: str = ITEM + "A") -> str:
    return line("WARNING", f"{url}: HTTP {status} (attempt 1/1)")


def completed(name: str = "A") -> str:
    return line("DEBUG", f"Completed: {name!r}")


def failed(name: str = "A") -> str:
    return line("WARNING", f"Failed: {name!r} — FetchError: page not found (404): x")


def feed(*lines: str):
    guard = gs.Guard()
    reasons = [guard.feed(x) for x in lines]
    return guard, reasons


# ── Worst case ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("f", "k", "worst"),
    [
        (0, 1, 1),  # nothing unheld: robots.txt at most
        (1, 1, 4),  # the step 7 probe
        (43, 1, 88),  # Updates 8-9, backlog plan "To continue"
        (258, 1, 518),  # batch 1 as written before the run
        (3, 5, 10),  # k above F: every page may pay its plain fetch
    ],
)
def test_worst_case_is_1_plus_2f_plus_min_f_k(f, k, worst):
    assert gs.worst_case(f, k) == worst


# ── 429/5xx rule ─────────────────────────────────────────────────────────────


def test_three_consecutive_throttles_stop_the_run():
    _, reasons = feed(throttled(429), throttled(503), throttled(500))
    assert reasons[:2] == [None, None]
    assert reasons[2] == "3 consecutive HTTP 429/5xx responses"


def test_a_stored_page_or_a_clear_plain_response_resets_the_count():
    _, reasons = feed(
        throttled(),
        throttled(),
        line("INFO", "Fetched 'Item:A' (28615 bytes, browser)"),
        throttled(),
        throttled(),
        line("DEBUG", f"GET {ITEM}B -> 200 (plain)"),
        throttled(),
        throttled(),
        line("DEBUG", f"GET {ITEM}C -> 202 (plain)"),
        throttled(),
    )
    assert reasons == [None] * 10


def test_a_plain_throttled_get_and_its_warning_count_once():
    lines = []
    for _ in range(3):
        lines += [line("DEBUG", f"GET {ITEM}A -> 503 (plain)"), throttled()]
    guard, reasons = feed(*lines)
    assert reasons[:5] == [None] * 5
    assert reasons[5] is not None
    assert guard.requests == 3


def test_browser_gets_carry_no_status_and_neither_count_nor_reset():
    guard, reasons = feed(
        throttled(),
        line("DEBUG", f"GET {ITEM}A (browser)"),
        line("DEBUG", f"GET {ITEM}A (browser)"),
        throttled(),
        line("DEBUG", "blocked script https://ddowiki.com/load.php?x=1 (browser)"),
        throttled(),
    )
    assert reasons[-1] is not None
    assert guard.requests == 2  # "blocked" lines are not requests


def test_other_request_problems_are_not_throttles():
    _, reasons = feed(
        line("WARNING", f"{ITEM}A: ConnectionError: boom (attempt 1/1)"),
        line("WARNING", f"{ITEM}A: HTTP 404 (attempt 1/1)"),
        line("WARNING", f"{ITEM}A: HTTP 403 (attempt 1/1)"),
    )
    assert reasons == [None, None, None]


def test_real_page_store_warnings_are_recognised(tmp_path):
    """The rule reads the Page Store's own retry warnings, as `--verbose` writes them."""
    config = load_scraper_config(_write_config(tmp_path, max_retries=2))
    plain = CannedTransport().reply(
        ITEM + "A", *[Response(status=s) for s in (429, 502, 503)]
    )
    lines: list[str] = []
    sink = logger.add(lines.append, format=FORMAT, level="DEBUG")
    try:
        with PageStore(config, transport=plain, sleep=lambda _s: None) as store:
            with pytest.raises(Exception, match="gave up"):
                store.get(ITEM + "A")
    finally:
        logger.remove(sink)
    guard, reasons = feed(*lines)
    assert reasons[-1] == "3 consecutive HTTP 429/5xx responses"
    assert guard.throttled_in_a_row == 3


def test_browser_5xx_warnings_are_recognised(tmp_path, monkeypatch):
    """A 503 in the real browser adapter (on a fake Playwright) counts like a plain one."""
    page = FakeBrowserPage()
    page.documents[ITEM + "A"] = [(503, "<html>Service Unavailable</html>")]
    monkeypatch.setitem(
        sys.modules, "playwright.sync_api", fake_playwright_module(page)
    )
    config = load_scraper_config(_write_config(tmp_path, max_retries=2))
    plain = CannedTransport(default=lambda _u: CHALLENGE)
    lines: list[str] = []
    sink = logger.add(lines.append, format=FORMAT, level="DEBUG")
    try:
        with PageStore(config, transport=plain, sleep=lambda _s: None) as store:
            with pytest.raises(Exception, match="gave up"):
                store.get(ITEM + "A")
    finally:
        logger.remove(sink)
    guard, reasons = feed(*lines)
    assert reasons[-1] == "3 consecutive HTTP 429/5xx responses"
    assert guard.throttled_in_a_row == 3


def test_coloured_lines_from_a_real_log_are_read():
    def coloured(time_: str, level: str, colour: str, msg: str) -> str:
        esc = "\x1b"
        return (
            f"{esc}[32m2026-09-24 {time_}{esc}[0m | "
            f"{colour}{esc}[1m{level:<8}{esc}[0m | {msg}"
        )

    real = [
        coloured(
            "09:41:09",
            "INFO",
            "",
            "Fetched 'Item:Mark of Sheshka' (28615 bytes, browser)",
        ),
        coloured(
            "09:41:12",
            "DEBUG",
            "\x1b[34m",
            "GET https://ddowiki.com/page/Item:Legendary_Mark_of_Sheshka (browser)",
        ),
        coloured(
            "09:41:13",
            "WARNING",
            "\x1b[33m",
            "Failed: 'Legendary Mark of Sheshka' — ExtractionError: no infobox table\n",
        ),
    ]
    guard, _ = feed(*real)
    assert (guard.requests, guard.processed, guard.failed) == (1, 1, 1)


# ── Failure-rate rule ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ok_items", "failures", "stops"),
    [
        (1, 1, False),  # 1 of 2
        (0, 2, False),  # 2 of 2: below the floor of 3
        (0, 3, True),  # 3 of 3
        (27, 3, False),  # 3 of 30 is 10%, not over
        (26, 3, True),  # 3 of 29
        (200, 22, False),  # 22 of 222
        (196, 22, True),  # 22 of 218
    ],
)
def test_failed_items_over_10_percent_stop_once_three_failed(ok_items, failures, stops):
    guard, reasons = feed(*[completed()] * ok_items, *[failed()] * failures)
    assert (guard.processed, guard.failed) == (ok_items + failures, failures)
    assert (reasons[-1] is not None) is stops


def test_a_skipped_page_is_processed_not_failed():
    guard, reasons = feed(
        line("INFO", "Skipped: 'Mark of Sheshka' — not equipment: Raw ingredients"),
        completed("Mark of Sheshka"),
        failed(),
        failed(),
        line("INFO", "Skipped: 'Token' — not equipment: Ingredients"),
        completed("Token"),
    )
    assert (guard.processed, guard.failed) == (4, 2)
    assert reasons == [None] * 6


def test_a_saved_item_whose_mark_complete_failed_counts_as_failed():
    guard, _ = feed(
        line("ERROR", "Item 'A' saved but mark_complete failed: QueueDbError: locked")
    )
    assert (guard.processed, guard.failed) == (1, 1)


def test_update_page_failures_are_not_items():
    guard, _ = feed(line("ERROR", "Failed to sync update page 'Update_8': boom"))
    assert (guard.processed, guard.failed) == (0, 0)


# ── Guarded run, with a fake sync child ──────────────────────────────────────


def _child(body: str) -> list[str]:
    return [sys.executable, "-c", body]


def _printer(lines: list[str], then: str) -> str:
    return (
        "import signal, sys, time\n"
        + "".join(f"print({x!r}, flush=True)\n" for x in lines)
        + then
    )


def test_the_guard_interrupts_a_throttled_sync(tmp_path):
    log = tmp_path / "sync.log"
    start = time.monotonic()
    code, reason = gs.run_guarded(
        _child(_printer([throttled()] * 3, "time.sleep(60)\n")), log, grace=30
    )
    assert time.monotonic() - start < 20
    assert code == gs.GUARD_STOPPED
    assert reason.startswith("guard stopped the sync: 3 consecutive HTTP 429/5xx")
    assert "KeyboardInterrupt" in log.read_text(encoding="utf-8")  # it was a SIGINT


def test_a_sync_that_ignores_sigint_is_terminated(tmp_path):
    body = _printer(
        [failed()] * 3,
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\ntime.sleep(60)\n",
    )
    # ignore SIGINT before printing, so the guard's SIGINT cannot race the handler
    body = "import signal\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n" + body
    start = time.monotonic()
    code, reason = gs.run_guarded(_child(body), tmp_path / "sync.log", grace=0.5)
    assert time.monotonic() - start < 20
    assert code == gs.GUARD_STOPPED
    assert "3 of 3 processed items failed" in reason


def test_an_unstopped_sync_keeps_its_exit_code_and_log(tmp_path):
    log = tmp_path / "sync.log"
    lines = [completed(), failed(), line("DEBUG", f"GET {ITEM}A -> 200 (plain)")]
    code, reason = gs.run_guarded(_child(_printer(lines, "sys.exit(2)\n")), log)
    assert code == 2
    assert reason.startswith("sync exited 2 (2 processed, 1 failed, 1 wiki GET lines)")
    assert log.read_text(encoding="utf-8").splitlines() == lines


# ── Dry run ──────────────────────────────────────────────────────────────────


def _write_config(tmp_path: Path, max_retries: int = 0, k: int = 1) -> Path:
    path = tmp_path / "scraper.yaml"
    path.write_text(
        'user_agent: "test"\n'
        f"max_retries: {max_retries}\n"
        "respect_robots_txt: false\n"
        f"cache_dir: {tmp_path / 'pages'}\n"
        f"browser:\n  enabled: true\n  consecutive_challenges: {k}\n",
        encoding="utf-8",
    )
    return path


def _hold(config_path: Path, *urls: str) -> None:
    plain = CannedTransport(default=lambda _u: ok("<html>held</html>"))
    with PageStore(
        load_scraper_config(config_path), transport=plain, sleep=lambda _s: None
    ) as store:
        for url in urls:
            store.get(url)


def _queue(path: Path, rows: dict[str, list[str]], synced: bool = True) -> None:
    """*rows*: update page -> item names, all pending."""
    with QueueRepository(str(path)) as repo:
        for page, names in rows.items():
            repo.register_update_page(page, update_page_url(page))
            if synced:
                repo.mark_page_synced(page, datetime.now(timezone.utc), 1)
            repo.enqueue_items(
                [
                    ItemLink(item_name=n, wiki_url=ITEM + n, update_page=page)
                    for n in names
                ]
            )


@pytest.fixture
def dry_setup(tmp_path):
    """Updates 8 and 9 read and held; 5 pending rows, 2 of them held."""
    config = _write_config(tmp_path)
    queue = tmp_path / "queue.db"
    _queue(
        queue,
        {"Update_8_named_items": ["A", "B", "C"], "Update_9_named_items": ["D", "E"]},
    )
    _hold(
        config,
        update_page_url("Update_8_named_items"),
        update_page_url("Update_9_named_items"),
    )
    _hold(config, ITEM + "B", ITEM + "E")
    return config, queue


def _dry(capsys, config, queue, *args) -> list[str]:
    code = gs.dry_run(
        ["--scraper-config", str(config), "--queue-db", str(queue), *args]
    )
    assert code == 0
    return capsys.readouterr().out.splitlines()


def test_dry_run_counts_unheld_pending_rows(dry_setup, capsys):
    config, queue = dry_setup
    out = _dry(
        capsys, config, queue, "--page", "Update_8_named_items", "--max-retries", "0"
    )
    assert "pending rows processed (--limit none): 5, unheld: 3" in out
    assert out[-1] == "F = 3; worst case 1 + 2F + min(F, k) = 8 requests"


def test_dry_run_takes_the_limit_in_queue_order(dry_setup, capsys):
    config, queue = dry_setup
    out = _dry(capsys, config, queue, "--page", "Update_9_named_items", "--limit", "2")
    assert "pending rows processed (--limit 2): 2, unheld: 1" in out  # A, B
    assert out[-1] == "F = 1; worst case 1 + 2F + min(F, k) = 4 requests"


def test_dry_run_bounds_an_unread_update_page(dry_setup, capsys):
    config, queue = dry_setup
    out = _dry(capsys, config, queue, "--page", "Update_14_named_items")
    assert "update pages read: 3, unheld: 1 (Update_14_named_items)" in out
    assert out[-1] == "F >= 4; worst case 1 + 2F + min(F, k) >= 10 requests"
    out = _dry(
        capsys, config, queue, "--page", "Update_14_named_items", "--limit", "10"
    )
    assert out[-1] == "F <= 11; worst case 1 + 2F + min(F, k) <= 24 requests"


def test_dry_run_resets_failed_rows_as_the_sync_would(dry_setup, capsys):
    config, queue = dry_setup
    with QueueRepository(str(queue)) as repo:
        first = repo.get_pending_items(limit=1)[0]
        repo.mark_failed(first.id, datetime.now(timezone.utc), "boom")
    args = ("--page", "Update_8_named_items")
    out = _dry(capsys, config, queue, *args, "--max-retries", "0")
    assert out[-1].startswith("F = 2;")
    out = _dry(capsys, config, queue, *args)  # the sync's default --max-retries 3
    assert out[-1].startswith("F = 3;")


def test_dry_run_leaves_the_queue_db_unchanged(dry_setup, capsys):
    config, queue = dry_setup
    before = queue.read_bytes()
    _dry(capsys, config, queue, "--page", "Update_14_named_items", "--max-retries", "3")
    assert queue.read_bytes() == before
    with sqlite3.connect(queue) as conn:
        assert conn.execute("SELECT COUNT(*) FROM update_pages").fetchone()[0] == 2


def test_dry_run_without_a_queue_db_starts_fresh(tmp_path, capsys):
    config = _write_config(tmp_path, max_retries=2)
    queue = tmp_path / "missing.db"
    out = _dry(capsys, config, queue, "--page", "Update_8_named_items", "--limit", "5")
    assert out[0] == f"no queue DB at {queue}: the sync would start a fresh one."
    assert "F <= 6; worst case 1 + 2F + min(F, k) <= 14 requests" in out
    assert out[-1].endswith("up to 3 x 14 = 42 requests.")
    assert not queue.exists()


def test_dry_run_warns_when_k_is_above_one(tmp_path, capsys):
    config = _write_config(tmp_path, k=5)
    out = _dry(capsys, config, tmp_path / "q.db", "--page", "Update_8_named_items")
    assert "k = browser.consecutive_challenges = 5" in out
    assert out[-1].startswith("warning: k > 1")


def test_dry_run_needs_pages(tmp_path, capsys):
    config = _write_config(tmp_path)
    assert gs.dry_run(["--scraper-config", str(config)]) == gs.USAGE_ERROR
    assert "--dry needs --page" in capsys.readouterr().out
