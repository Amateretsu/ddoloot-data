"""Fetch a stratified random sample of wiki item pages into the local HTML cache.

Reads the pending/complete item URLs from a ddo_sync queue database, picks up to
``--count`` items spread across as many different update pages as possible, and saves
each page's HTML under ``cache/html/`` (gitignored) so parsing can be developed offline.

A plain fetch is tried first. An AWS WAF challenge is never treated as a successful fetch;
per ADR 0006 the rest of the run then uses a real browser (Playwright, unmodified browser
identity, no proxies, no CAPTCHA services). If the browser page still shows a challenge
the run stops. Pages already cached are skipped.

Usage:
    python scripts/sample_pages.py --queue-db PATH [--count 40] [--seed 1] [--pause 4]
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "html"
USER_AGENT = "DDOLoot-data-sampler (+https://github.com/Amateretsu/ddoloot-data)"


def pick_sample(rows: list[tuple[str, str, str]], count: int, seed: int) -> list[tuple[str, str, str]]:
    """Round-robin across shuffled update pages so the sample spans many updates."""
    rng = random.Random(seed)
    by_update: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in rows:
        by_update[row[2]].append(row)
    updates = list(by_update)
    rng.shuffle(updates)
    for items in by_update.values():
        rng.shuffle(items)
    picked: list[tuple[str, str, str]] = []
    while len(picked) < count and any(by_update.values()):
        for update in updates:
            if by_update[update] and len(picked) < count:
                picked.append(by_update[update].pop())
    return picked


def cache_name(url: str) -> str:
    title = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in title) + ".html"


def fetch_with_browser(page, url: str) -> str | None:
    """Load a page in a real browser; return its HTML, or None if still challenged."""
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_selector("#mw-content-text", timeout=45_000)
    except Exception:
        return None
    return page.content()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--queue-db", required=True, type=Path)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--pause", type=float, default=4.0, help="seconds between requests (minimum 4, ADR 0006)"
    )
    args = parser.parse_args()

    with sqlite3.connect(args.queue_db) as conn:
        rows = conn.execute("SELECT item_name, wiki_url, update_page FROM scrape_queue").fetchall()
    sample = pick_sample(rows, args.count, args.seed)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    index_path = CACHE_DIR.parent / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    playwright = browser = page = None
    try:
        for n, (name, url, update) in enumerate(sample, 1):
            fname = cache_name(url)
            if (CACHE_DIR / fname).exists():
                print(f"[{n}/{len(sample)}] cached  {name}")
                continue
            html, via = None, "plain"
            if page is None:
                resp = session.get(url, timeout=30)
                if resp.status_code == 200 and "x-amzn-waf-action" not in resp.headers:
                    html = resp.text
                elif resp.status_code == 202 or "x-amzn-waf-action" in resp.headers:
                    print("WAF challenge on plain fetch; switching to browser for this run.")
                    from playwright.sync_api import sync_playwright

                    playwright = sync_playwright().start()
                    browser = playwright.chromium.launch()
                    page = browser.new_page()
                else:
                    print(f"[{n}/{len(sample)}] HTTP {resp.status_code}  {name}")
                    continue
            if page is not None and html is None:
                via = "browser"
                html = fetch_with_browser(page, url)
                if html is None:
                    print(f"Challenge not cleared in browser at {url}; stopping.")
                    return 1
            (CACHE_DIR / fname).write_text(html, encoding="utf-8")
            index[fname] = {"name": name, "url": url, "update_page": update, "bytes": len(html), "via": via}
            index_path.write_text(json.dumps(index, indent=2, sort_keys=True))
            print(f"[{n}/{len(sample)}] saved   {name}  ({update}, {via})")
            time.sleep(max(4.0, args.pause))
    finally:
        if browser:
            browser.close()
        if playwright:
            playwright.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
