# page_store

The **Page Store** (see `CONTEXT.md`) is the local copy of wiki pages that the catalog build reads from. It fetches a page from ddowiki.com only when it does not already hold that page, and it follows the acquisition policy in ADR 0006 (app repo, `docs/adr/0006-wiki-acquisition-policy.md`).

---

## Interface

```python
from page_store import PageStore, load_scraper_config

with PageStore(load_scraper_config()) as store:          # config/scraper.yaml
    page = store.get("https://ddowiki.com/page/Item:Breaker_of_Bodies")
    page.html, page.url, page.title, page.fetched_at, page.via

    fresh = store.get(page.url, refresh=True)            # refetch a held page

    for cached in store.iter_cached():                   # every held page, by title; no network
        ...
```

| Member | Description |
|---|---|
| `get(url, refresh=False) -> CachedPage` | Returns the held copy of the page, or fetches it if the store does not hold it (or if `refresh=True`). A held page costs no request. |
| `iter_cached() -> Iterator[CachedPage]` | Yields every held page, ordered by title. It never touches the network. |
| `close()` / context manager | Releases the transports (HTTP session, browser). |

`CachedPage` is a frozen dataclass with these fields: `html`, `url`, `title` (for example `Item:Breaker of Bodies`), `fetched_at` (UTC) and `via` (`"plain"` or `"browser"`).

`get()` only accepts `https://ddowiki.com/page/<title>` article URLs. It raises `ValueError` for `/api.php`, `index.php`, `Special:` pages, query strings and other hosts, and makes no request.

URLs that name the same title share one copy. For example, `…/page/Item:A%27s_B` and `…/page/Item:A's B` both resolve to the title `Item:A's B`.

### Errors

| Exception | Meaning | What callers do |
|---|---|---|
| `FetchError(url, status)` | This page could not be fetched: a 404, another 4xx, retries used up, or robots.txt disallows it. | Record the page as failed and carry on. |
| `RunStoppedError` | The policy says the whole run must stop: robots.txt is unreadable with `robots_fail_open: false`, or the browser fallback needs Playwright and it is missing. | Stop the run, and do **not** mark the page failed. |
| `ChallengeError(url)` (a `RunStoppedError`) | A WAF challenge hit with the browser fallback disabled, or the challenge was still there in the browser. | Same as `RunStoppedError`. |

All three inherit from `PageStoreError`.

---

## Acquisition policy (what the module does behind `get()`)

- **Pacing.** One request at a time, at least `crawl_delay_seconds` apart. Retries are paced too. robots.txt's `Crawl-delay` is used when it is larger. The floor is 4 s: a config below 4 is rejected when it is loaded, and the store clamps the delay to at least 4 as well.
- **robots.txt.** It is read once per run (per `PageStore` instance), and only when the run has to fetch a page: a run served wholly from the store sends no request at all. It is read through the plain transport, with the configured user agent, and evaluated per RFC 9309 by the module's own parser. Every group whose user-agent value appears in our product token (the leading word of `user_agent`, e.g. `ddoloot-data`, ignoring case) applies; if none does, the `*` groups apply. Rule paths are literal prefixes of the raw path and query, with `*` and a trailing `$`. The longest match wins, and on a tie `Allow` wins. `urllib.robotparser` is not used, because it rewrites ddowiki's `Disallow: /?` to `Disallow: /` and so blocks the whole site. A 4xx means there is no robots.txt, so every page is allowed. If robots.txt cannot be read (a network error, 5xx or a challenge), `robots_fail_open` decides: `true` continues with a warning, `false` raises `RunStoppedError`. A disallowed page raises `FetchError`.
- **Retries.** Connection errors, timeouts, HTTP 429 and 5xx are retried up to `max_retries` times. Each retry waits twice as long as the one before (8 s, 16 s, 32 s at the default delay). A 404 or any other 4xx is not retried.
- **Challenge detection.** A response is a WAF challenge if it is HTTP 202 or carries an `x-amzn-waf-action` header. A challenge is never cached and never counted as a successful fetch.
- **Browser fallback** (`browser.enabled: true`):
  - A challenged plain fetch is retried through the browser adapter.
  - The run switches to the browser for all remaining pages after `consecutive_challenges` challenged plain fetches in a row (default 5).
  - It also switches when more than `challenge_ratio` of plain fetches were challenged (default 0.2). The ratio only applies once at least `RATIO_MIN_SAMPLE` (10) plain fetches have been made. Below that, only the consecutive rule applies.
  - Each new `PageStore` instance (each run) starts with plain fetching again.
  - A page that is still challenged in the browser raises `ChallengeError`.
- **Browser disabled** (`browser.enabled: false`): the first challenge raises `ChallengeError`.
- **A challenge is never retried.** Challenge detection comes before the retry rules, so a challenge carrying a 403 or 5xx status is not retried either: it costs one plain request, then the browser.
- **No TTL.** A held page is only refetched when the caller passes `refresh=True`.

---

## Configuration: `config/scraper.yaml`

```yaml
user_agent: "DDOLoot-data (+https://github.com/Amateretsu/ddoloot-data)"
crawl_delay_seconds: 4        # floor 4, enforced when loading
timeout_seconds: 30
max_retries: 3
respect_robots_txt: true
robots_fail_open: true
cache_dir: ../cache/pages     # relative to this file
browser:
  enabled: true               # the wiki challenges every plain fetch today
  consecutive_challenges: 5
  challenge_ratio: 0.2
```

`load_scraper_config(path=None)` validates the file into a frozen `ScraperConfig`. It raises `ScraperConfigError` for an unreadable file, an unknown key, a bad value or a crawl delay below 4. A relative `cache_dir` is resolved against the directory that holds the config file.

The `ddoloot` CLI reads this file, or the one given with `--scraper-config PATH`.

### Browser fallback setup

The committed config enables the browser fallback, because the wiki WAF-challenges every plain fetch (checked live on 2026-09-24). Headless Chromium clears the challenge, with the identified user agent. The browser adapter is the optional extra `browser`:

```bash
pip install -e ".[browser]"
playwright install chromium
```

The extra caps Playwright below 1.62, because later releases ship no Chromium for macOS 13. Playwright is imported only when the first page is sent to the browser, so everything else (CI included) works without it. If `browser.enabled` is true but Playwright is not installed, the first challenge raises `RunStoppedError` with installation instructions.

### What a fetch costs the wiki

Run with `--verbose` to see every request sent to the wiki: each adapter logs one `GET <url> … (plain)` or `GET <url> (browser)` DEBUG line per request, redirects included.

| Run | Requests |
|---|---|
| every page already held | 0 |
| first fetch of a run | robots.txt 1 + plain 1 (challenged) + browser 2 (the challenged document, then its reload once the challenge clears) = 4 |
| each later challenged page in the same run | plain 1 + browser 1 or 2 |

Retries add requests only for connection errors, timeouts, 429 and 5xx, never for a challenge.

---

## On disk

```
<cache_dir>/
  index.json                                     # title -> {url, file, fetched_at, via}
  Item_Breaker_of_Bodies-<8 hex>.html
  Update_5_named_items-<8 hex>.html
```

- Filenames are a readable slug of the title, followed by the first 8 hex digits of the SHA-1 of the title. Titles that produce the same slug (`Item:A'B` and `Item:A B`) therefore get different files.
- `index.json` only records what is cached. The crawl ledger (what should be fetched, and what failed) is the SQLite queue in `ddo_sync`.
- Files are written atomically (temporary file, then rename).
- The cache is disposable and gitignored under `cache/`. The default `cache/pages/` is separate from the retired `cache/html/` + `cache/index.json` layout. Those old files are neither migrated nor read.

---

## Seams and adapters

The transport is the seam. It has one method, `fetch(url) -> Response(status, text, headers)`, plus `close()`. A transport raises `TransportError` when no response arrives.

| Adapter | Use |
|---|---|
| `HttpTransport(user_agent, timeout_seconds)` | Plain `requests` fetch, the production default. |
| `page_store.browser.BrowserTransport(user_agent, timeout_seconds)` | Real, unmodified headless Chromium through Playwright, used for the fallback. Same user agent and pace, no proxies, stealth plugins, fingerprint spoofing or CAPTCHA services. From the wiki it may request only `/page/` documents, at most `MAX_WIKI_REQUESTS` (2) per fetch: the challenged page and its reload. Every other wiki request (`load.php`, images, `api.php`, further reloads) is aborted before it is sent; requests to the AWS WAF challenge hosts go through. The HTML returned is the article as the server rendered it. A page whose content (`#mw-content-text`) never appears comes back as a challenge response. |
| canned responses | Tests (`tests/canned.py`). |

```python
PageStore(config, transport=None, browser=None, sleep=time.sleep)
```

The browser is only used when `config.browser.enabled` is true. Tests pass a recording `sleep`, so that pacing is checked without waiting.
