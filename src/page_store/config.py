"""Scraper policy: the typed form of ``config/scraper.yaml``.

The policy is data the Page Store module reads; it has no behaviour of its own. Loading
enforces ADR 0006's floor: a crawl delay below 4 seconds is rejected, never clamped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

#: ADR 0006: never fetch faster than one page per 4 seconds.
MIN_CRAWL_DELAY_SECONDS = 4.0

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scraper.yaml"


class ScraperConfigError(ValueError):
    """``scraper.yaml`` is missing, malformed, or breaks the acquisition policy."""


class BrowserPolicy(BaseModel):
    """When a challenged run falls back to, and then switches to, the browser adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    consecutive_challenges: int = Field(default=5, ge=1)
    challenge_ratio: float = Field(default=0.2, gt=0.0, le=1.0)


class ScraperConfig(BaseModel):
    """Acquisition policy for one Page Store (see ``config/scraper.yaml``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_agent: str = Field(min_length=1)
    crawl_delay_seconds: float = Field(default=4.0, ge=MIN_CRAWL_DELAY_SECONDS)
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    respect_robots_txt: bool = True
    robots_fail_open: bool = True
    cache_dir: Path
    browser: BrowserPolicy = BrowserPolicy()


def load_scraper_config(path: Optional[Path] = None) -> ScraperConfig:
    """Read and validate a scraper policy file.

    Args:
        path: YAML file; defaults to ``config/scraper.yaml`` at the repo root. A relative
            ``cache_dir`` is resolved against the directory holding this file.

    Raises:
        ScraperConfigError: The file is unreadable, has unknown keys or bad values, or
            sets ``crawl_delay_seconds`` below 4.
    """
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ScraperConfigError(f"cannot read scraper config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScraperConfigError(f"scraper config {path} must be a mapping")
    try:
        config = ScraperConfig.model_validate(raw)
    except ValidationError as exc:
        raise ScraperConfigError(f"invalid scraper config {path}:\n{exc}") from exc
    if not config.cache_dir.is_absolute():
        config = config.model_copy(
            update={"cache_dir": (path.parent / config.cache_dir).resolve()}
        )
    return config
