"""Run the named-item backlog at the bulk-fetch cadence, one guarded sync at a time.

Usage (from the ddoloot-data repo root):
    .venv/bin/python scripts/bulk_fetch.py --log-dir DIR [--budget 1200] [--limit 100]
        [--pause 1200] [--max-runs N] [--scraper-config config/scraper-bulk.yaml]
        [--queue-db data/queue.db]
    .venv/bin/python scripts/bulk_fetch.py --dry

Cadence and rules: docs/plans/2026-09-24-bulk-fetch-readiness.md (Step 6, "To continue",
"Bulk fetch: ready-to-run") and the budget in docs/plans/2026-09-24-catalog-src-backlog.md.

Each run is one ``scripts/guarded_sync.py`` run (imported, not duplicated): its stop
guard, its log and its exit code. Before each run:

* the queue is read from a copy made through a read-only connection (guarded_sync's
  ``queue_plan``), so this script never writes ``data/queue.db``; only the syncs do;
* the run's ``--page`` list is every update page with pending rows, in ascending update
  number, plus, when those rows are fewer than ``--limit``, the next page of
  ``config/update_pages.yaml`` (ascending N) not yet read into the queue;
* guarded_sync's dry-run arithmetic gives the worst case. If it exceeds the budget left
  (``--budget`` less the ``GET`` lines in this invocation's run logs), the loop stops.

After a run: exit 0 or 2 (failed items remain) continue after a ``--pause`` (none if the
run sent no wiki request); exit 1 (run stopped) or 3 (guard stopped) stop the loop with
the Step 6 advice; anything else stops too. The loop also stops at ``--max-runs``, when
the backlog is complete, when a new update page was still not read after its run, and on
Ctrl-C. ``--dry`` prints the next run's pages and worst case and sends nothing.

Exit code: 0 backlog complete, ``--max-runs`` reached or ``--dry``; 1 any other stop;
130 Ctrl-C; 64 usage error.
"""

# ruff: noqa: T201  (a command-line script reports on stdout)
from __future__ import annotations

import argparse
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import yaml
from loguru import logger

from ddo_sync.cli import QUEUE_DB
from ddo_sync.discovery import UPDATE_PAGES_PATH

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guarded_sync as gs  # the sibling script, reused by import

REPO = Path(__file__).resolve().parents[1]
BULK_CONFIG = REPO / "config" / "scraper-bulk.yaml"
SESSION_BUDGET = 1200  # catalog-src-backlog plan, "Budget and live rules"
RUN_LIMIT = 100  # bulk-fetch-readiness plan, Step 6: at most 100 items per run
PAUSE_SECONDS = 1200  # Step 6: 20 minutes between runs
STOPPED, INTERRUPTED = 1, 130

STEP6_ADVICE = (
    "Stop for the day (at least several hours). Read the `no article …` line in the run "
    "log: a 403 or 405 means stop until the maintainer decides (ADR 0006 admin "
    "override). If the challenge fails again at this cadence, recommend "
    "crawl_delay_seconds: 8 to the maintainer."
)

_UPDATE_NUMBER = re.compile(r"Update_(\d+)_named_items")


# ── Pure logic ───────────────────────────────────────────────────────────────


def page_order(name: str) -> tuple:
    """Ascending update number, pages with no number last by name (the queue's order)."""
    match = _UPDATE_NUMBER.fullmatch(name)
    return (0, int(match.group(1)), name) if match else (1, 0, name)


@dataclass
class QueueView:
    """What the driver needs from the queue: pending rows per page and read pages."""

    pending: dict[str, int] = field(default_factory=dict)  # page -> pending rows
    read: set[str] = field(default_factory=set)  # pages whose links were read


def choose_pages(seed: Sequence[str], view: QueueView, limit: int) -> list[str]:
    """The next run's ``--page`` list; empty when the backlog is complete."""
    pages = sorted((p for p, n in view.pending.items() if n > 0), key=page_order)
    if sum(view.pending.values()) < limit:
        unread = sorted((p for p in seed if p not in view.read), key=page_order)
        if unread:
            pages.append(unread[0])
    return pages


def budget_stop(plan: gs.WorstCase, remaining: int) -> Optional[str]:
    """Why the next run may not start within *remaining* requests, or None."""
    most = plan.most_requests
    if most is None:
        return "the worst case has no upper bound (a --limit is needed)"
    if most > remaining:
        return f"worst case {most} requests exceeds the {remaining} left in the budget"
    return None


def exit_stop(code: int) -> Optional[str]:
    """Why guarded_sync's exit *code* stops the loop, or None to continue."""
    if code in (0, 2):  # 2: completed with failed items remaining
        return None
    if code == 1:
        return f"the sync stopped (exit 1, e.g. an uncleared challenge). {STEP6_ADVICE}"
    if code == gs.GUARD_STOPPED:
        return f"the guard stopped the sync (exit 3). {STEP6_ADVICE}"
    return f"unexpected exit code {code} from guarded_sync"


@dataclass
class RunTally:
    requests: int
    completed: int  # skipped pages included, as the sync counts them
    failed: int
    skipped: int


def tally_log(path: Path) -> RunTally:
    """Count a run log's wiki ``GET`` lines and item outcomes (guarded_sync's reading)."""
    guard, skipped = gs.Guard(), 0
    if path.exists():
        with open(path, encoding="utf-8", errors="replace") as log:
            for line in log:
                guard.feed(line)
                skipped += gs.message(line).startswith("Skipped: ")
    return RunTally(
        guard.requests, guard.processed - guard.failed, guard.failed, skipped
    )


# ── Seams (replaced in tests) ────────────────────────────────────────────────


def read_seed(path: Path = UPDATE_PAGES_PATH) -> list[str]:
    return [str(t) for t in yaml.safe_load(path.read_text(encoding="utf-8")) or []]


def read_queue(queue_db: Path) -> QueueView:
    """The queue as it stands, read from guarded_sync's read-only copy."""
    pages, pending = gs.queue_plan(queue_db, [], None, 0)  # 0: resets no row
    view = QueueView(read={p.page_name for p in pages if p.last_synced_at})
    for item in pending:
        view.pending[item.update_page] = view.pending.get(item.update_page, 0) + 1
    return view


@dataclass
class Seams:
    read_seed: Callable[[], list[str]] = read_seed
    read_queue: Callable[[Path], QueueView] = read_queue
    plan: Callable[[Sequence[str]], gs.WorstCase] = gs.worst_case_plan
    run: Callable[[Sequence[str], Path], tuple[int, str]] = gs.run_guarded
    sleep: Callable[[float], None] = time.sleep
    interrupted: Callable[[], bool] = lambda: False


# ── The loop ─────────────────────────────────────────────────────────────────


def sync_args(opts: argparse.Namespace, pages: Sequence[str]) -> list[str]:
    args = ["--scraper-config", str(opts.scraper_config)]
    if opts.queue_db:
        args += ["--queue-db", str(opts.queue_db)]
    return [*args, "--page", *pages, "--limit", str(opts.limit), "--max-retries", "0"]


def next_log(log_dir: Path) -> tuple[int, Path]:
    """The next free ``run-NNN.log``, so a restarted driver keeps earlier logs."""
    numbers = [
        int(p.stem[4:]) for p in log_dir.glob("run-*.log") if p.stem[4:].isdigit()
    ]
    n = max(numbers, default=0) + 1
    return n, log_dir / f"run-{n:03d}.log"


def run_loop(opts: argparse.Namespace, seams: Seams) -> int:
    queue_db = opts.queue_db or QUEUE_DB
    seed = seams.read_seed()
    spent, runs, last_requests = 0, 0, 0
    code, reason = STOPPED, ""
    new_page: Optional[str] = None
    before: Optional[QueueView] = None
    try:
        while True:
            view = seams.read_queue(queue_db)
            if new_page and new_page not in view.read:
                reason = f"{new_page} was not read into the queue by the last run"
                break
            if view == before:
                reason = "the last run changed nothing in the queue"
                break
            if opts.max_runs is not None and runs >= opts.max_runs:
                code, reason = 0, f"--max-runs {opts.max_runs} reached"
                break
            pages = choose_pages(seed, view, opts.limit)
            if not pages:
                code, reason = 0, "backlog complete: nothing pending, no unread page"
                break
            args = sync_args(opts, pages)
            plan = seams.plan(args)
            stop = budget_stop(plan, opts.budget - spent)
            if opts.dry:
                print(f"next run --page: {' '.join(pages)}")
                for text in plan.lines:
                    print(text)
                print(f"budget left: {opts.budget - spent}; " + (stop or "fits"))
                code, reason = 0, "dry run: no request sent"
                break
            if stop:
                reason = stop
                break
            if last_requests:
                print(f"pausing {opts.pause:g} s before the next run")
                seams.sleep(opts.pause)
            new_page = pages[-1] if pages[-1] not in view.pending else None
            before = view
            n, log = next_log(opts.log_dir)
            print(
                f"run {n}: --page {' '.join(pages)} (worst case {plan.most_requests})"
            )
            exit_code, detail = seams.run([*gs.SYNC_COMMAND, *args], log)
            t = tally_log(log)
            runs, spent, last_requests = runs + 1, spent + t.requests, t.requests
            line = (
                f"run {n}: pages {' '.join(pages)}; requests {t.requests}; "
                f"completed {t.completed} (skipped {t.skipped}), failed {t.failed}; "
                f"exit {exit_code}; log {log.name}"
            )
            print(line)
            print(f"  {detail}")
            with open(opts.log_dir / "summary.log", "a", encoding="utf-8") as out:
                out.write(line + "\n")
            if seams.interrupted():
                code, reason = INTERRUPTED, "interrupted (Ctrl-C)"
                break
            stop = exit_stop(exit_code)
            if stop:
                reason = stop
                break
    except KeyboardInterrupt:
        code, reason = INTERRUPTED, "interrupted (Ctrl-C)"
    _final_summary(opts, seams, queue_db, runs, spent, reason)
    return code


def _final_summary(opts, seams: Seams, queue_db: Path, runs, spent, reason) -> None:
    print(f"stopped: {reason}")
    if opts.dry:
        return
    view = seams.read_queue(queue_db)
    pending = sorted((p for p, n in view.pending.items() if n), key=page_order)
    print(f"runs: {runs}; requests: {spent} of the {opts.budget} budget")
    print(
        "pages still pending: "
        + (", ".join(f"{p} ({view.pending[p]})" for p in pending) or "none")
    )
    done = " ".join(sorted(view.read, key=page_order))
    print(
        "Before committing, verify offline (bulk-fetch-readiness plan, To continue, "
        "step 3):\n"
        f"  .venv/bin/python scripts/offline_rerun.py {done}\n"
        "  UPDATE_GAPS_BASELINE=1 .venv/bin/pytest -q tests/item_extractor "
        "-k whole_page_store\n"
        "  then review the item-file diff, run the four CI checks and "
        "check_catalog('catalog-src'), confirm a second offline_rerun.py leaves "
        "git status unchanged, and commit."
    )


# ── Entry point ──────────────────────────────────────────────────────────────


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--log-dir", type=Path, help="Run logs and summary.log.")
    p.add_argument("--budget", type=int, default=SESSION_BUDGET)
    p.add_argument("--limit", type=int, default=RUN_LIMIT)
    p.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    p.add_argument("--max-runs", type=int)
    p.add_argument("--scraper-config", type=Path, default=BULK_CONFIG)
    p.add_argument("--queue-db", type=Path)
    p.add_argument(
        "--dry", action="store_true", help="Plan the next run; send nothing."
    )
    opts = p.parse_args(list(argv))
    if not opts.dry and opts.log_dir is None:
        p.error("--log-dir is required unless --dry")
    return opts


def main(argv: Sequence[str]) -> int:
    logger.remove()  # the queue copy's DEBUG lines are not the report; runs log to files
    try:
        opts = parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else gs.USAGE_ERROR
    if opts.log_dir:
        opts.log_dir.mkdir(parents=True, exist_ok=True)
    flag: list[bool] = []

    def on_sigint(_sig, _frame):
        flag.append(True)  # guarded_sync passes it on to the sync as one SIGINT
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_sigint)
    return run_loop(opts, Seams(interrupted=lambda: bool(flag)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
