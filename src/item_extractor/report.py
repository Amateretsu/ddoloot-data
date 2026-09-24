"""Aggregate per-page extraction reports into one run report."""

from __future__ import annotations

from collections import Counter
from typing import Any


def aggregate(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Combine per-page reports (keyed by page name) into counts and review lists."""
    templates: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    unmapped: dict[str, list[str]] = {}
    ignored: Counter[str] = Counter()
    unclassified: dict[str, list[str]] = {}
    for page, rep in reports.items():
        templates[rep["template"]] += 1
        rules.update(rep["rule_hits"])
        ignored.update(rep["ignored_rows"])
        for label in rep["unmapped_rows"]:
            unmapped.setdefault(label, []).append(page)
        for text in rep["unclassified_effects"]:
            unclassified.setdefault(text, []).append(page)
    return {
        "pages": len(reports),
        "templates": dict(templates),
        "rule_hits": dict(rules),
        "ignored_rows": dict(ignored),
        "unmapped_rows": unmapped,
        "unclassified_effects": unclassified,
    }
