"""The UUID registry, through Registry.load(), id_for() and save(), on files in tmp_path."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from catalog_registry import Registry

UUID4 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
ID_A = "0b6f7a3e-2c1d-4e5f-8a9b-1c2d3e4f5a6b"
ID_B = "9f8e7d6c-5b4a-4c3d-9e2f-1a0b9c8d7e6f"
COMMITTED = Path(__file__).resolve().parents[2] / "catalog-src" / "registry.jsonl"


def line(entry_id: str, page_id: int, title: str) -> str:
    return json.dumps({"id": entry_id, "page_id": page_id, "title": title}) + "\n"


def write(path: Path, *lines: str) -> Path:
    path.write_text("".join(lines), encoding="utf-8")
    return path


def read_entries(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]


def test_missing_file_loads_empty_and_save_creates_it(tmp_path):
    path = tmp_path / "nested" / "registry.jsonl"
    registry = Registry.load(path)

    registry.save()

    assert path.read_text(encoding="utf-8") == ""


def test_new_page_gets_a_lowercase_hyphenated_uuid4(tmp_path):
    registry = Registry.load(tmp_path / "registry.jsonl")

    named_item_id = registry.id_for(12387, "Item:Boots of Corrosion")

    assert UUID4.fullmatch(named_item_id)


def test_same_page_id_returns_the_same_uuid(tmp_path):
    registry = Registry.load(tmp_path / "registry.jsonl")

    first = registry.id_for(1, "Item:A")

    assert registry.id_for(1, "Item:A") == first


def test_different_page_ids_get_different_uuids(tmp_path):
    registry = Registry.load(tmp_path / "registry.jsonl")

    assert registry.id_for(1, "Item:A") != registry.id_for(2, "Item:B")


def test_existing_entry_is_matched_by_page_id(tmp_path):
    path = write(tmp_path / "registry.jsonl", line(ID_A, 5, "Item:A"))

    assert Registry.load(path).id_for(5, "Item:A") == ID_A


def test_same_title_with_another_page_id_gets_a_new_uuid(tmp_path):
    path = write(tmp_path / "registry.jsonl", line(ID_A, 5, "Item:A"))

    assert Registry.load(path).id_for(6, "Item:A") != ID_A


def test_rename_keeps_the_uuid_and_updates_the_stored_title(tmp_path):
    path = write(tmp_path / "registry.jsonl", line(ID_A, 5, "Item:Old Name"))
    registry = Registry.load(path)

    assert registry.id_for(5, "Item:New Name") == ID_A
    registry.save()

    assert read_entries(path) == [{"id": ID_A, "page_id": 5, "title": "Item:New Name"}]


def test_save_sorts_by_page_id_and_keeps_every_entry(tmp_path):
    path = write(
        tmp_path / "registry.jsonl", line(ID_B, 30, "Item:C"), line(ID_A, 10, "Item:A")
    )
    registry = Registry.load(path)
    new_id = registry.id_for(20, "Item:B")

    registry.save()

    assert read_entries(path) == [
        {"id": ID_A, "page_id": 10, "title": "Item:A"},
        {"id": new_id, "page_id": 20, "title": "Item:B"},
        {"id": ID_B, "page_id": 30, "title": "Item:C"},
    ]


def test_minted_ids_survive_a_save_and_reload(tmp_path):
    path = tmp_path / "registry.jsonl"
    registry = Registry.load(path)
    minted = {
        page_id: registry.id_for(page_id, f"Item:{page_id}") for page_id in (3, 1, 2)
    }
    registry.save()

    reloaded = Registry.load(path)

    assert {
        page_id: reloaded.id_for(page_id, f"Item:{page_id}") for page_id in minted
    } == minted


def test_save_writes_one_compact_line_per_entry_with_unicode_kept(tmp_path):
    path = tmp_path / "registry.jsonl"
    registry = Registry.load(path)
    named_item_id = registry.id_for(7, "Item:Gnôme's Cap")

    registry.save()

    assert path.read_text(encoding="utf-8") == (
        f'{{"id": "{named_item_id}", "page_id": 7, "title": "Item:Gnôme\'s Cap"}}\n'
    )


def test_unchanged_registry_saves_byte_identical(tmp_path):
    original = line(ID_A, 1, "Item:A") + line(ID_B, 2, "Item:B")
    path = write(tmp_path / "registry.jsonl", original)
    registry = Registry.load(path)
    registry.id_for(2, "Item:B")

    registry.save()

    assert path.read_text(encoding="utf-8") == original


def test_nothing_is_written_before_save(tmp_path):
    path = write(tmp_path / "registry.jsonl", line(ID_A, 1, "Item:A"))
    registry = Registry.load(path)

    registry.id_for(2, "Item:B")
    registry.id_for(1, "Item:Renamed")

    assert path.read_text(encoding="utf-8") == line(ID_A, 1, "Item:A")


def test_blank_lines_are_skipped(tmp_path):
    path = write(tmp_path / "registry.jsonl", "\n", line(ID_A, 1, "Item:A"), "  \n")

    assert Registry.load(path).id_for(1, "Item:A") == ID_A


@pytest.mark.parametrize(
    "bad_line",
    [
        "not json\n",
        "[1, 2]\n",
        json.dumps({"id": ID_A, "page_id": 1}) + "\n",
        json.dumps({"id": ID_A, "page_id": 1, "title": "A", "extra": 1}) + "\n",
        line(ID_A.upper(), 1, "Item:A"),
        line("0b6f7a3e2c1d4e5f8a9b1c2d3e4f5a6b", 1, "Item:A"),
        line("0b6f7a3e-2c1d-1e5f-8a9b-1c2d3e4f5a6b", 1, "Item:A"),
        json.dumps({"id": ID_A, "page_id": "1", "title": "Item:A"}) + "\n",
        json.dumps({"id": ID_A, "page_id": True, "title": "Item:A"}) + "\n",
        json.dumps({"id": ID_A, "page_id": 1, "title": None}) + "\n",
    ],
)
def test_malformed_line_is_rejected_with_its_line_number(tmp_path, bad_line):
    path = write(tmp_path / "registry.jsonl", line(ID_B, 2, "Item:B"), bad_line)

    with pytest.raises(ValueError, match=r"registry\.jsonl:2"):
        Registry.load(path)


def test_duplicate_page_id_is_rejected(tmp_path):
    path = write(
        tmp_path / "registry.jsonl", line(ID_A, 1, "Item:A"), line(ID_B, 1, "Item:B")
    )

    with pytest.raises(ValueError, match="duplicate page_id"):
        Registry.load(path)


def test_duplicate_uuid_is_rejected(tmp_path):
    path = write(
        tmp_path / "registry.jsonl", line(ID_A, 1, "Item:A"), line(ID_A, 2, "Item:B")
    )

    with pytest.raises(ValueError, match="duplicate id"):
        Registry.load(path)


@pytest.mark.parametrize(
    ("page_id", "title"),
    [(None, "Item:A"), ("12", "Item:A"), (True, "Item:A"), (1, None)],
)
def test_id_for_rejects_a_bad_page_id_or_title(tmp_path, page_id, title):
    registry = Registry.load(tmp_path / "registry.jsonl")

    with pytest.raises(TypeError):
        registry.id_for(page_id, title)


def test_committed_registry_loads():
    Registry.load(COMMITTED)

    assert COMMITTED.exists()
