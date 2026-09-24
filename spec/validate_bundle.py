"""Validate an unpacked data bundle against the v1 spec.

Usage: python spec/validate_bundle.py <bundle_dir> [--strict]

Checks JSON Schema validity of every file, then referential integrity:
effect and bonus-type keys, shared option lists, constraint slot ids,
redirect targets, unique ids, and meta counts.
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SPEC_DIR = Path(__file__).parent / "v1"

# file name -> (schema file, is_list_of_entries)
FILES = {
    "items.json": ("item.schema.json", True),
    "effects.json": ("effect.schema.json", True),
    "bonus-types.json": ("bonus-type.schema.json", True),
    "option-lists.json": ("option-list.schema.json", True),
    "servers.json": ("server.schema.json", True),
    "redirects.json": ("redirect.schema.json", True),
    "slot-compat.json": ("slot-compat.schema.json", False),
    "meta.json": ("meta.schema.json", False),
}


def _close(node):
    """Return a copy of a schema with additionalProperties: false on every object."""
    if isinstance(node, dict):
        out = {k: _close(v) for k, v in node.items()}
        if out.get("type") == "object" and "properties" in out:
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(node, list):
        return [_close(v) for v in node]
    return node


def _registry() -> Registry:
    registry = Registry()
    for path in SPEC_DIR.glob("*.schema.json"):
        contents = json.loads(path.read_text())
        registry = registry.with_resource(contents["$id"], Resource.from_contents(contents))
    return registry


def validate_bundle(bundle_dir: Path, strict: bool = False) -> list[str]:
    """Return a list of human-readable errors; empty means the bundle is valid.

    Schemas are open (consumers ignore unknown fields). With strict=True every
    object rejects unknown properties; the data repo uses this to lint its own output.
    """
    errors: list[str] = []
    registry = _registry()
    data: dict[str, object] = {}
    for filename, (schema_name, is_list) in FILES.items():
        path = bundle_dir / filename
        if not path.exists():
            errors.append(f"{filename}: missing")
            continue
        data[filename] = json.loads(path.read_text())
        schema = json.loads((SPEC_DIR / schema_name).read_text())
        if strict:
            schema = _close(schema)
        validator = Draft202012Validator(schema, registry=registry)
        entries = data[filename] if is_list else [data[filename]]
        for i, entry in enumerate(entries):
            for err in validator.iter_errors(entry):
                where = f"[{i}]" if is_list else ""
                errors.append(f"{filename}{where}: {'/'.join(map(str, err.path))}: {err.message}")
    if errors:
        return errors
    return _integrity_errors(data)


def _integrity_errors(data: dict[str, object]) -> list[str]:
    errors: list[str] = []
    items, effects = data["items.json"], data["effects.json"]
    effect_keys = {e["key"] for e in effects}
    bonus_keys = {b["key"] for b in data["bonus-types.json"]}
    list_ids = {o["id"] for o in data["option-lists.json"]}
    item_ids = [i["id"] for i in items]
    for label, values in (
        ("item id", item_ids),
        ("effect key", [e["key"] for e in effects]),
        ("bonus type key", [b["key"] for b in data["bonus-types.json"]]),
        ("option list id", [o["id"] for o in data["option-lists.json"]]),
    ):
        if len(values) != len(set(values)):
            errors.append(f"duplicate {label}")
    for item in items:
        name = item["name"]
        for eff in item.get("effects", []):
            if eff["effect"] not in effect_keys:
                errors.append(f"{name}: unknown effect {eff['effect']!r}")
            if eff.get("bonus_type") and eff["bonus_type"] not in bonus_keys:
                errors.append(f"{name}: unknown bonus type {eff['bonus_type']!r}")
        slot_ids = [s["id"] for s in item.get("customisation_slots", [])]
        if len(slot_ids) != len(set(slot_ids)):
            errors.append(f"{name}: duplicate customisation slot id")
        for slot in item.get("customisation_slots", []):
            if "options" not in slot:
                if "augments" not in list_ids:
                    errors.append(f"{name}: colour slot needs an 'augments' option list")
                if slot["colour"] not in data["slot-compat.json"]["colour_compat"]:
                    errors.append(f"{name}: slot colour {slot['colour']!r} not in colour_compat")
                continue
            shared = slot["options"].get("shared")
            if shared and shared not in list_ids:
                errors.append(f"{name}: unknown shared option list {shared!r}")
            if slot["min"] > slot["max"]:
                errors.append(f"{name}: slot {slot['id']!r} min > max")
        for con in item.get("constraints", []):
            refs = [con["if_slot"], con["then_slot"]] if con["type"] == "requires" else con["slots"]
            for sid in refs:
                if sid not in slot_ids:
                    errors.append(f"{name}: constraint references unknown slot {sid!r}")
    live = set(item_ids)
    for red in data["redirects.json"]:
        if red["from"] in live:
            errors.append(f"redirect from live item {red['from']}")
        if red["to"] is not None and red["to"] not in live:
            errors.append(f"redirect to unknown item {red['to']}")
    meta = data["meta.json"]["counts"]
    for key, filename in (
        ("items", "items.json"), ("effects", "effects.json"), ("bonus_types", "bonus-types.json"),
        ("option_lists", "option-lists.json"), ("servers", "servers.json"), ("redirects", "redirects.json"),
    ):
        if meta[key] != len(data[filename]):
            errors.append(f"meta.counts.{key} is {meta[key]}, file has {len(data[filename])}")
    return errors


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--strict"]
    if len(args) != 1:
        print(__doc__)
        return 2
    errors = validate_bundle(Path(args[0]), strict="--strict" in argv)
    for err in errors:
        print(err)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
