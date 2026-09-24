# ddowiki_scraper

HTTP client for fetching item pages from [ddowiki.com](https://ddowiki.com) ethically. Handles rate limiting, exponential-backoff retries, and optional robots.txt enforcement.

---

## Quick Start

```python
from ddowiki_scraper import WikiFetcher, WikiFetcherConfig

config = WikiFetcherConfig(rate_limit_delay=2.5, max_retries=3)
with WikiFetcher(config) as fetcher:
    html = fetcher.fetch_item_page("Mantle_of_the_Worldshaper")
```

---

## WikiFetcherConfig

Pydantic model that validates and holds all configuration for `WikiFetcher`.

```python
from ddowiki_scraper import WikiFetcherConfig

config = WikiFetcherConfig(
    base_url="https://ddowiki.com",   # Wiki base URL
    user_agent="ddoloot/0.1",         # Identifies the scraper
    rate_limit_delay=2.5,             # Seconds between requests (1.0–10.0)
    max_retries=3,                    # Retry attempts on failure (1–10)
    timeout=30,                       # Request timeout in seconds (5–120)
    max_concurrent=3,                 # Max concurrent async requests (1–10)
    respect_robots_txt=True,          # Check robots.txt before scraping
)
```

### `get_request_headers() → dict[str, str]`

Returns the HTTP headers that will be sent with every request, including `User-Agent`, `Accept`, and `DNT`.

---

## WikiFetcher

The main HTTP client. Supports synchronous fetching and async batch fetching. Use as a context manager to ensure the underlying session is properly closed.

```python
from ddowiki_scraper import WikiFetcher, WikiFetcherConfig

with WikiFetcher(WikiFetcherConfig()) as fetcher:
    ...
```

### Synchronous Methods

#### `fetch_item_page(item_name: str) → str`

Fetches the HTML for a single item page. `item_name` is the page slug as it appears in the URL (spaces replaced with underscores).

```python
html = fetcher.fetch_item_page("Sword_of_Shadow")
```

- Automatically enforces the configured rate limit delay.
- Retries on transient HTTP errors using exponential backoff.

**Raises:**
- `FetchError` — HTTP request failed after all retries
- `RobotsTxtError` — robots.txt disallows the URL (when `respect_robots_txt=True`)

#### `fetch_multiple(item_names: list[str]) → dict[str, str]`

Fetches multiple items sequentially, returning a mapping of item name → HTML string. Respects rate limits between each request.

```python
pages = fetcher.fetch_multiple([
    "Mantle_of_the_Worldshaper",
    "Sword_of_Shadow",
])
```

### Asynchronous Methods

#### `fetch_item_page_async(item_name: str) → str`

Async equivalent of `fetch_item_page`. Requires an active event loop.

```python
import asyncio

async def main():
    with WikiFetcher(WikiFetcherConfig()) as fetcher:
        html = await fetcher.fetch_item_page_async("Sword_of_Shadow")

asyncio.run(main())
```

#### `fetch_multiple_async(item_names: list[str]) → dict[str, str]`

Fetches multiple items concurrently, up to `max_concurrent` at a time.

```python
async def main():
    with WikiFetcher(WikiFetcherConfig(max_concurrent=3)) as fetcher:
        pages = await fetcher.fetch_multiple_async([
            "Mantle_of_the_Worldshaper",
            "Sword_of_Shadow",
            "Lenses_of_Opportunity",
        ])
```

---

## RateLimiter

Enforces a minimum delay between requests. Used internally by `WikiFetcher` but can be used standalone.

```python
from ddowiki_scraper import RateLimiter

limiter = RateLimiter(min_delay=2.5)
limiter.wait()   # blocks if last call was < 2.5 seconds ago
```

---

## Exceptions

All exceptions inherit from `WikiFetcherError`.

| Exception | When raised |
|---|---|
| `WikiFetcherError` | Base class for all scraper errors |
| `FetchError` | HTTP request failed after all retries |
| `RateLimitError` | Rate limit configuration is invalid |
| `RobotsTxtError` | robots.txt disallows the requested URL |

```python
from ddowiki_scraper import FetchError, RobotsTxtError

try:
    html = fetcher.fetch_item_page("Some_Item")
except RobotsTxtError as e:
    print(f"Blocked by robots.txt: {e}")
except FetchError as e:
    print(f"Network error: {e}")
```

---

## Scraping Ethics

- The default `rate_limit_delay` of **2.5 seconds** keeps request rates well within what ddowiki.com can comfortably handle.
- Set `respect_robots_txt=True` (the default) to automatically skip URLs that the site's `robots.txt` disallows.
- The `user_agent` string identifies DDOLoot by name so site operators can contact the project if needed.
- Do not set `rate_limit_delay` below `1.0` or `max_concurrent` above `3` for production use.
