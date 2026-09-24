"""Run one ``ddoloot sync --verbose`` under the bulk-run stop guard, or print its worst case.

Usage (from the ddoloot-data repo root):
    .venv/bin/python scripts/guarded_sync.py --log PATH [sync args...]
    .venv/bin/python scripts/guarded_sync.py --dry [sync args...]
Every argument other than ``--log`` and ``--dry`` is passed to ``ddoloot sync --verbose``,
e.g. ``--scraper-config S --page Update_8_named_items --limit 60 --max-retries 0``.

**Guarded run.** Starts the sync as a subprocess (in its own session), writes all of its
output to ``--log`` and reads it line by line as it is written. It stops the sync with
SIGINT, the same as Ctrl-C (the queue keeps its progress), when either:

* 3 consecutive wiki responses are HTTP 429 or 5xx. Each one is the Page Store's
  ``<url>: HTTP <status> (attempt i/n)`` warning, logged for both the plain and the
  browser adapter, retries included. A plain ``GET <url> -> <status> (plain)`` with any
  other status, or a ``Fetched '<title>'`` line (a page stored, either adapter), resets
  the run. A browser ``GET <url> (browser)`` line carries no status, so it neither counts
  nor resets. A browser 404 therefore does not reset the run either, which can only stop
  a run sooner.
* failed items exceed 10% of processed items, once at least 3 have failed:
  ``failed >= 3 and failed * 10 > processed``. Processed is ``Completed: '<name>'`` plus
  failed; failed is ``Failed: '<name>' — ...`` or ``... saved but mark_complete failed``.
  A skipped page (``Skipped: ...``, not equipment) is followed by its ``Completed:`` line,
  so it counts as processed and not as failed. The floor of 3 keeps 1 failure out of 2
  (or 2 out of 2) from stopping a run, and still stops 3 failures in a row at once.

If the sync has not exited ``GRACE_SECONDS`` after the SIGINT, its process group gets
SIGTERM, then SIGKILL. Ctrl-C on this script is passed on to the sync as one SIGINT.

Exit code: the sync's own (0 complete, 1 stopped or fatal, 2 failed items remain), or
``GUARD_STOPPED`` (3) when the guard stopped it. Usage errors exit 64. The last line
printed gives the reason, the items processed and failed, and the wiki GET lines
counted in the log (the request tally, as ``grep -c 'GET '`` counts it).

**Dry run** (``--dry``, needs ``--page``). Sends no request at all: it builds the Page
Store with transports that raise and a no-op sleep, only asks it for pages it holds, and
reads a copy of the queue DB. It prints the worst case ``1 + 2F + min(F, k)``:

* 1 is robots.txt;
* F is the pages the sync could fetch that the Page Store does not hold:
  - the update pages it reads (every page already in the queue, plus ``--page``) that are
    not held, and
  - the pending queue rows it would process that are not held. These are the rows
    ``get_pending_items(--limit)`` returns, in the sync's order, after the sync's own
    start-of-run ``reset_failed_to_pending(--max-retries)``. Both are run through the
    queue's interface on a temporary copy of the DB (default ``data/queue.db``, or
    ``--queue-db``), opened read-only; the real file is never written;
* 2F: each page is at most one browser load, which the browser adapter lets send 2 wiki
  requests (the challenged document and its reload);
* min(F, k): before the run switches to the browser, each challenged page also costs its
  plain fetch. ``k`` is ``browser.consecutive_challenges`` from the scraper config, the
  number of challenged plain fetches in a row that switch the rest of the run to the
  browser. The bound is exact for k = 1, as in the live scratch config. With k > 1, a
  mix of challenged and clear plain fetches can cost a few more, so a warning is printed.

The formula assumes ``max_retries: 0`` in the scraper config. Otherwise each request may
repeat up to ``1 + max_retries`` times, and that bound is printed too.

An update page that is not held, or held but not yet read into the queue, adds item rows
nobody can count offline. The dry run then prints ``F >= ...`` (a lower bound), or, when
``--limit`` is given, the upper bound with ``--limit`` rows all unheld. ``--refresh``
counts every page as unheld.
"""

# ruff: noqa: T201  (a command-line script reports on stdout)
from __future__ import annotations

import argparse
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from loguru import logger

from ddo_sync.cli import QUEUE_DB
from ddo_sync.discovery import update_page_url
from ddo_sync.queue_db import QueueRepository
from page_store import PageStore, load_scraper_config

GUARD_STOPPED = 3
USAGE_ERROR = 64
MAX_THROTTLED_IN_A_ROW = 3
MIN_FAILED = 3
GRACE_SECONDS = 60.0
KILL_AFTER_SECONDS = 10.0

SYNC_COMMAND = [
    sys.executable,
    "-c",
    "import sys; from ddo_sync.cli import main; sys.exit(main())",
    "sync",
    "--verbose",
]

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_PLAIN_STATUS = re.compile(r"GET \S+ -> (\d{3}) \(plain\)$")
_THROTTLED = re.compile(r"\S+: HTTP (?:429|5\d\d) \(attempt \d+/\d+\)$")


# ── Stop logic (pure) ────────────────────────────────────────────────────────


def message(line: str) -> str:
    """The message part of a ``<time> | <LEVEL> | <message>`` log line, colours removed."""
    parts = _ANSI.sub("", line).rstrip("\r\n").split(" | ", 2)
    return parts[2] if len(parts) == 3 else parts[0]


@dataclass
class Guard:
    """Reads a ``sync --verbose`` log one line at a time and says when to stop it."""

    processed: int = 0
    failed: int = 0
    throttled_in_a_row: int = 0
    requests: int = 0

    def feed(self, line: str) -> Optional[str]:
        """Take one log line; return the stop reason once a stop rule holds, else None."""
        msg = message(line)
        if msg.startswith("GET "):
            self.requests += 1
            status = _PLAIN_STATUS.match(msg)
            if status and not _is_throttle(int(status.group(1))):
                self.throttled_in_a_row = 0
        elif _THROTTLED.fullmatch(msg):
            self.throttled_in_a_row += 1
        elif msg.startswith("Fetched '"):
            self.throttled_in_a_row = 0
        elif msg.startswith("Completed: "):
            self.processed += 1
        elif msg.startswith("Failed: ") or " saved but mark_complete failed: " in msg:
            self.processed += 1
            self.failed += 1
        return self.stop_reason()

    def stop_reason(self) -> Optional[str]:
        if self.throttled_in_a_row >= MAX_THROTTLED_IN_A_ROW:
            return f"{self.throttled_in_a_row} consecutive HTTP 429/5xx responses"
        if self.failed >= MIN_FAILED and self.failed * 10 > self.processed:
            return (
                f"{self.failed} of {self.processed} processed items failed (over 10%)"
            )
        return None

    def tally(self) -> str:
        return (
            f"{self.processed} processed, {self.failed} failed, "
            f"{self.requests} wiki GET lines"
        )


def worst_case(f: int, k: int) -> int:
    """Most wiki requests a sync fetching *f* unheld pages can send (max_retries 0)."""
    return 1 + 2 * f + min(f, k)


def _is_throttle(status: int) -> bool:
    return status == 429 or status >= 500


# ── Guarded run ──────────────────────────────────────────────────────────────


def run_guarded(
    command: Sequence[str], log_path: Path, grace: float = GRACE_SECONDS
) -> tuple[int, str]:
    """Run *command*, logging to *log_path*, stopping it when a stop rule holds.

    Returns ``(exit code, one-line reason)``.
    """
    guard = Guard()
    stop: list[str] = []
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=True,
        )

        def on_line(line: str) -> None:
            log.write(line)
            log.flush()
            if not stop:
                reason = guard.feed(line)
                if reason:
                    stop.append(reason)
                    _interrupt(proc, grace)

        try:
            _pump(proc, on_line)
        except KeyboardInterrupt:
            _interrupt(proc, grace)
            _pump(proc, on_line)
        code = proc.wait()
    if stop:
        return GUARD_STOPPED, (
            f"guard stopped the sync: {stop[0]} (sync exit {code}; {guard.tally()}); "
            f"log: {log_path}"
        )
    return code, f"sync exited {code} ({guard.tally()}); log: {log_path}"


def _pump(proc: subprocess.Popen, on_line: Callable[[str], None]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line)


def _interrupt(proc: subprocess.Popen, grace: float) -> None:
    """SIGINT the sync; escalate on its process group if it is still running later."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    threading.Thread(target=_escalate, args=(proc, grace), daemon=True).start()


def _escalate(proc: subprocess.Popen, grace: float) -> None:
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, KILL_AFTER_SECONDS)):
        try:
            proc.wait(wait)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return


# ── Dry run ──────────────────────────────────────────────────────────────────


class _NoNetwork(Exception):
    """Raised by the dry run's transports: the page is not held, and is never fetched."""


class _Refuse:
    def fetch(self, url: str):
        raise _NoNetwork(url)

    def close(self) -> None:
        pass


def _sync_options(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    p.add_argument("--page", nargs="+", dest="pages")
    p.add_argument("--limit", type=int)
    p.add_argument("--queue-db", type=Path, dest="queue_db")
    p.add_argument("--scraper-config", type=Path, dest="scraper_config")
    p.add_argument("--max-retries", type=int, default=3, dest="max_retries")
    p.add_argument("--refresh", action="store_true")
    return p.parse_known_args(list(argv))[0]


def queue_plan(
    queue_db: Path, pages: Sequence[str], limit: Optional[int], max_retries: int
):
    """Update pages the sync reads and rows it processes, from a copy of *queue_db*.

    The copy is made from a read-only (``mode=ro``) connection, so *queue_db* is never
    written. A missing *queue_db* reads as an empty queue.
    """
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "queue.db"
        if queue_db.exists():
            src = sqlite3.connect(f"{queue_db.resolve().as_uri()}?mode=ro", uri=True)
            dst = sqlite3.connect(copy)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
        with QueueRepository(str(copy)) as repo:
            for name in pages:
                name = name.replace(" ", "_")
                repo.register_update_page(name, update_page_url(name))
            repo.reset_failed_to_pending(max_retries)
            return repo.list_update_pages(), repo.get_pending_items(limit=limit)


@dataclass
class WorstCase:
    """The dry run's result: its report lines and the bound on wiki requests."""

    lines: list[str]
    f: int
    bound: str  # "=" exact, "<=" upper bound (``--limit``), ">=" lower bound only
    worst: int  # 1 + 2F + min(F, k)
    attempts: int  # 1 + max_retries from the scraper config

    @property
    def most_requests(self) -> Optional[int]:
        """Most wiki requests the run can send, retries included; None if unbounded."""
        return None if self.bound == ">=" else self.worst * self.attempts


def worst_case_plan(sync_args: Sequence[str]) -> WorstCase:
    """Work out a sync's worst case with no request sent (``--page`` required)."""
    logger.remove()  # the queue copy's DEBUG lines are not the report
    opts = _sync_options(sync_args)
    lines: list[str] = []
    config = load_scraper_config(opts.scraper_config)
    k = config.browser.consecutive_challenges
    queue_db = opts.queue_db or QUEUE_DB
    if not queue_db.exists():
        lines.append(f"no queue DB at {queue_db}: the sync would start a fresh one.")
    update_pages, pending = queue_plan(
        queue_db, opts.pages, opts.limit, opts.max_retries
    )
    with PageStore(
        config, transport=_Refuse(), browser=_Refuse(), sleep=lambda _s: None
    ) as store:

        def held(url: str) -> bool:
            if opts.refresh:
                return False
            try:
                store.get(url)
            except _NoNetwork:
                return False
            return True

        unheld_pages = [
            p.page_name for p in update_pages if not held(update_page_url(p.page_name))
        ]
        unread = [
            p.page_name
            for p in update_pages
            if p.page_name in unheld_pages or p.last_synced_at is None
        ]
        unheld_rows = [i for i in pending if not held(i.wiki_url)]

    limit = "none" if opts.limit is None else opts.limit
    lines.append(
        f"update pages read: {len(update_pages)}, unheld: {len(unheld_pages)}"
        + (f" ({', '.join(unheld_pages)})" if unheld_pages else "")
    )
    lines.append(
        f"pending rows processed (--limit {limit}): {len(pending)}, "
        f"unheld: {len(unheld_rows)}"
    )
    f = len(unheld_pages) + len(unheld_rows)
    bound = "="
    if unread:
        lines.append(
            f"not yet read into the queue: {', '.join(unread)}; "
            "their item rows are not known offline."
        )
        if opts.limit is None:
            bound = ">="
        else:
            f, bound = len(unheld_pages) + opts.limit, "<="
            lines.append(f"upper bound: all {opts.limit} processed rows unheld.")
    worst = worst_case(f, k)
    lines.append(f"k = browser.consecutive_challenges = {k}")
    lines.append(
        f"F {bound} {f}; worst case 1 + 2F + min(F, k) {bound} {worst} requests"
    )
    if k > 1:
        lines.append(
            "warning: k > 1; mixed challenged and clear plain fetches may cost more."
        )
    attempts = 1 + config.max_retries
    if config.max_retries:
        lines.append(
            f"warning: max_retries {config.max_retries} in the scraper config; with "
            f"retries, up to {attempts} x {worst} = {attempts * worst} requests."
        )
    return WorstCase(lines, f, bound, worst, attempts)


def dry_run(sync_args: Sequence[str]) -> int:
    if not _sync_options(sync_args).pages:
        print(
            "--dry needs --page: discovery would read the index and every update page."
        )
        return USAGE_ERROR
    for text in worst_case_plan(sync_args).lines:
        print(text)
    return 0


# ── Entry point ──────────────────────────────────────────────────────────────


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    dry = "--dry" in args
    if dry:
        args.remove("--dry")
        return dry_run(args)
    if "--log" not in args or args.index("--log") + 1 >= len(args):
        print(__doc__)
        return USAGE_ERROR
    i = args.index("--log")
    log_path = Path(args[i + 1])
    del args[i : i + 2]
    code, reason = run_guarded([*SYNC_COMMAND, *args], log_path)
    print(reason)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
