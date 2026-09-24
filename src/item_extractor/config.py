"""Load and validate the normalizer config (catalog/normalizer/*.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from item_extractor.coercers import COERCERS

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "catalog" / "normalizer"


class ConfigError(ValueError):
    """The normalizer config is malformed."""


@dataclass(frozen=True)
class Config:
    fields: dict[str, Any]
    templates: dict[str, Any]
    mappings: dict[str, Any]
    enchantments: dict[str, Any]
    #: normalised row label -> field entry
    label_index: dict[str, dict[str, Any]]
    ignored_labels: frozenset[str]
    ignored_patterns: tuple[re.Pattern[str], ...]
    effects_labels: frozenset[str]
    null_values: frozenset[str]


def normalize_label(text: str) -> str:
    """Lowercase, drop colons, and collapse whitespace (including NBSP)."""
    text = text.replace("\xa0", " ").rstrip(": ").strip().lower()
    return re.sub(r"\s+", " ", text)


def load_config(config_dir: Path | None = None) -> Config:
    """Read the four YAML files and build the lookup structures.

    Raises:
        ConfigError: on a missing file, an unknown coercer, an unknown template key,
            a duplicate label, or a rule pattern that does not compile.
    """
    config_dir = config_dir or DEFAULT_CONFIG_DIR
    loaded: dict[str, Any] = {}
    for name in ("fields", "templates", "mappings", "enchantments"):
        path = config_dir / f"{name}.yaml"
        if not path.exists():
            raise ConfigError(f"missing config file: {path}")
        loaded[name] = yaml.safe_load(path.read_text(encoding="utf-8"))

    label_index: dict[str, dict[str, Any]] = {}
    for entry in loaded["fields"]["fields"]:
        if entry["coerce"] not in COERCERS:
            raise ConfigError(
                f"unknown coercer {entry['coerce']!r} for {entry['target']!r}"
            )
        for label in entry["labels"]:
            key = normalize_label(label)
            if key in label_index:
                raise ConfigError(f"label {key!r} is mapped twice")
            label_index[key] = entry

    for rule in loaded["enchantments"]["rules"]:
        try:
            re.compile(rule["pattern"])
        except re.error as exc:
            raise ConfigError(f"rule {rule['id']!r}: bad pattern: {exc}") from exc

    if not loaded["templates"]["templates"][-1]["detect"].get("default"):
        raise ConfigError("the last template must be the default")

    ignore = loaded["fields"].get("ignore", {})
    return Config(
        fields=loaded["fields"],
        templates=loaded["templates"],
        mappings=loaded["mappings"],
        enchantments=loaded["enchantments"],
        label_index=label_index,
        ignored_labels=frozenset(normalize_label(x) for x in ignore.get("labels", [])),
        ignored_patterns=tuple(
            re.compile(p, re.I) for p in ignore.get("label_patterns", [])
        ),
        effects_labels=frozenset(
            normalize_label(x) for x in loaded["fields"]["effects_labels"]
        ),
        null_values=frozenset(x.lower() for x in loaded["fields"]["null_values"]),
    )
