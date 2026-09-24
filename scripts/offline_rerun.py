"""No-network re-run of `ddoloot sync` for the given update pages.

Usage (from the ddoloot-data repo root):
    .venv/bin/python scripts/offline_rerun.py Update_5_named_items Update_6_named_items ...
    [--scraper-config PATH]   (default: config/scraper.yaml; only its cache_dir matters)
In zsh pass the titles literally or as an array ("${P[@]}"); an unsplit "$P" becomes one
bogus title, which the harness skips offline but which re-processes nothing.

Runs the real CLI main in-process with a fresh temporary queue DB, so only pages the
Page Store already holds are re-processed. ``ddo_sync.cli.PageStore`` is patched so the
plain and browser transports raise on any fetch and sleep is a no-op. Item files and the
registry are written to the real catalog-src (as a live run would), reports to
cache/extracted. Prints the number of fetch attempts; exits 1 if any was made.
"""

# ruff: noqa: T201  (a command-line script reports on stdout)
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path.cwd()
sys.path.insert(0, str(REPO / "src"))

import ddo_sync.cli as cli  # noqa: E402
from page_store import PageStore  # noqa: E402

ATTEMPTS: list[str] = []


class NoNetwork(Exception):
    """Raised by the patched transports; not a TransportError, so it is never retried."""


class RaisingTransport:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def fetch(self, url: str):
        ATTEMPTS.append(f"{self.kind} {url}")
        raise NoNetwork(f"offline harness: {self.kind} fetch of {url}")

    def close(self) -> None:
        pass


NOT_HELD: list[str] = []


class NotHeld(Exception):
    """The page is not in the Page Store; the harness never fetches it."""


class OfflineStore(PageStore):
    """Serves held pages; an unheld page fails before robots.txt or any transport."""

    def _fetch(self, url: str):
        NOT_HELD.append(url)
        raise NotHeld(f"offline harness: {url} is not held")


def offline_store(config, *_args, **_kwargs) -> PageStore:
    return OfflineStore(
        config,
        transport=RaisingTransport("plain"),
        browser=RaisingTransport("browser"),
        sleep=lambda _s: None,
    )


def main(argv: list[str]) -> int:
    config_args: list[str] = []
    if "--scraper-config" in argv:
        i = argv.index("--scraper-config")
        config_args = argv[i : i + 2]
        argv = argv[:i] + argv[i + 2 :]
    pages = argv
    if not pages:
        print(__doc__)
        return 2
    with (
        tempfile.TemporaryDirectory() as tmp,
        mock.patch.object(cli, "PageStore", offline_store),
    ):
        rc = cli.main(
            [
                "sync",
                "--queue-db",
                str(Path(tmp) / "queue.db"),
                *config_args,
                "--max-retries",
                "0",
                "--page",
                *pages,
            ]
        )
    print(f"sync exit code: {rc}")
    print(f"fetch attempts: {len(ATTEMPTS)}")
    print(f"unheld pages skipped (no request): {len(NOT_HELD)}")
    for a in ATTEMPTS:
        print(f"  {a}")
    return 1 if ATTEMPTS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
