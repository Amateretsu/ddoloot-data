"""Extract cached wiki pages: python -m item_extractor CACHE_DIR --out OUT_DIR [--report FILE]."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from item_extractor import ExtractionError, aggregate, extract, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cache_dir", type=Path, help="directory holding html/ and index.json"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--config", type=Path, help="override catalog/extractor")
    args = parser.parse_args()

    cfg = load_config(args.config)
    index = json.loads((args.cache_dir / "index.json").read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    reports, failures = {}, []
    for fname, meta in sorted(index.items()):
        html = (args.cache_dir / "html" / fname).read_text(encoding="utf-8")
        try:
            item, report = extract(html, meta["url"], cfg)
        except ExtractionError as exc:
            failures.append({"page": meta["name"], "error": str(exc)})
            continue
        reports[meta["name"]] = report
        (args.out / fname.replace(".html", ".json")).write_text(
            json.dumps(
                item.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    summary = aggregate(reports) | {"failures": failures}
    text = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
