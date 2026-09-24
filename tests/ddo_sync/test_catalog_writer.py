"""CatalogWriter and check_catalog, through write() and check_catalog(catalog_src)."""

import json
import shutil

import pytest

from catalog_registry import Registry
from ddo_sync import CatalogWriter, check_catalog
from item_extractor import ScrapedItem, extract, load_config
from tests.ddo_sync.conftest import ITEM_PAGE_HTML, PAGES

URL = "https://ddowiki.com/page/Item:Breaker_of_Bodies"
VEST_URL = "https://ddowiki.com/page/Item:Dark_Ressurectionist%27s_Frock_Vest"
BREAKER_PAGE_ID = 61183
U8 = {"update_page": "Update_8_named_items"}


@pytest.fixture(scope="module")
def breaker():
    return extract(ITEM_PAGE_HTML, URL, load_config())


@pytest.fixture
def src(tmp_path):
    """A temporary catalog-src: registry.jsonl plus items/."""
    return tmp_path / "catalog-src"


@pytest.fixture
def registry(src):
    return Registry.load(src / "registry.jsonl")


@pytest.fixture
def writer(src, registry, tmp_path):
    return CatalogWriter(registry, src / "items", tmp_path / "extracted")


def _item_files(src):
    return sorted(p.relative_to(src / "items").as_posix() for p in src.rglob("*.json"))


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def _with(item, **changes):
    wiki = changes.pop("wiki", {})
    return item.model_copy(
        update={**changes, "wiki": item.wiki.model_copy(update=wiki)}
    )


# ── Item files ────────────────────────────────────────────────────────────────


def test_writes_the_item_file_under_update_category_and_uuid_slug(
    src, registry, writer, breaker
):
    item, report = breaker
    writer.write(item, {**U8, **report})

    named_item_id = registry.id_for(BREAKER_PAGE_ID, "Item:Breaker of Bodies")
    assert _item_files(src) == [
        f"update-8/shield/{named_item_id}-breaker-of-bodies.json"
    ]
    text = next(src.rglob("*.json")).read_text(encoding="utf-8")
    written = json.loads(text)
    assert next(iter(written)) == "id"
    assert written.pop("id") == named_item_id
    assert set(written) == set(ScrapedItem.model_fields), "every field is written"
    assert ScrapedItem.model_validate(written) == item
    assert (
        text
        == json.dumps({"id": named_item_id, **written}, indent=2, ensure_ascii=False)
        + "\n"
    )


def test_writing_again_changes_no_byte(src, writer, breaker):
    item, report = breaker
    writer.write(item, {**U8, **report})
    [path] = src.rglob("*.json")
    before = path.read_bytes()
    writer.write(item, {**U8, **report})
    assert _item_files(src) == [path.relative_to(src / "items").as_posix()]
    assert path.read_bytes() == before


def test_an_existing_registry_entry_gives_the_file_its_uuid(src, breaker, tmp_path):
    item, report = breaker
    seeded = Registry.load(src / "registry.jsonl")
    named_item_id = seeded.id_for(BREAKER_PAGE_ID, "Item:Old Name")
    seeded.save()

    registry = Registry.load(src / "registry.jsonl")
    CatalogWriter(registry, src / "items", tmp_path / "x").write(item, report)
    [path] = src.rglob("*.json")
    assert path.name.startswith(named_item_id)
    registry.save()
    assert json.loads((src / "registry.jsonl").read_text()) == {
        "id": named_item_id,
        "page_id": BREAKER_PAGE_ID,
        "title": "Item:Breaker of Bodies",
    }


def test_an_item_without_an_update_page_goes_to_unknown(src, writer, breaker):
    item, report = breaker
    writer.write(item, report)
    writer.write(item, {"update_page": "Update_50_revamped_named_items", **report})
    [name] = _item_files(src)
    assert name.startswith("unknown/shield/")


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        (["Update_8_named_items", "Update_5_named_items"], "update-5"),
        (["Update_5_named_items", "Update_8_named_items"], "update-5"),
        (["Update_10_named_items", "Update_9_named_items"], "update-9"),
        ([None, "Update_12_named_items"], "update-12"),
        (["Update_12_named_items", None], "update-12"),
    ],
)
def test_an_item_on_several_update_pages_keeps_one_file_under_the_lowest(
    src, writer, breaker, pages, expected
):
    item, report = breaker
    for page in pages:
        writer.write(item, {"update_page": page, **report})
    [name] = _item_files(src)
    assert name.split("/")[0] == expected
    assert sorted(p.name for p in (src / "items").iterdir()) == [expected]


def test_a_renamed_or_recategorised_item_is_moved_not_copied(src, writer, breaker):
    item, report = breaker
    writer.write(item, {**U8, **report})
    [old] = _item_files(src)
    named_item_id = old.split("/")[-1][:36]

    writer.write(
        _with(item, name="Breaker of Souls", category="weapon"), {**U8, **report}
    )
    assert _item_files(src) == [
        f"update-8/weapon/{named_item_id}-breaker-of-souls.json"
    ]
    assert not (src / "items" / "update-8" / "shield").exists()


@pytest.mark.parametrize(
    ("name", "category", "filename"),
    [
        ("Sword of Shadow!", "weapon", "weapon/{id}-sword-of-shadow.json"),
        (
            "  --Ring   of  Fire's Edge-- ",
            "jewelry",
            "jewelry/{id}-ring-of-fire-s-edge.json",
        ),
        ("Robe (Level 17)", "clothing", "clothing/{id}-robe-level-17.json"),
        ("Plate", "armor", "armor/{id}-plate.json"),
        ("Épée", "trinket", "other/{id}-p-e.json"),
        ("!!!", "other", "other/{id}-item.json"),
        (None, "shield", "shield/{id}-item.json"),
        ("a" * 59 + " b", "weapon", "weapon/{id}-" + "a" * 59 + ".json"),
        ("x" * 70, "weapon", "weapon/{id}-" + "x" * 60 + ".json"),
    ],
)
def test_category_and_slug_rules(src, writer, breaker, name, category, filename):
    item, report = breaker
    writer.write(_with(item, name=name, category=category), {**U8, **report})
    [written] = _item_files(src)
    named_item_id = written.split("/")[-1][:36]
    assert written == "update-8/" + filename.format(id=named_item_id)


def test_a_null_page_id_writes_no_file_and_reports_an_error(
    src, registry, writer, breaker, tmp_path
):
    item, report = breaker
    writer.write(_with(item, wiki={"page_id": None}), {**U8, **report})

    assert _item_files(src) == []
    registry.save()
    assert (src / "registry.jsonl").read_text() == ""
    [line] = _lines(tmp_path / "extracted" / "update-8" / "report.jsonl")
    assert line["url"] == URL
    assert "wiki.page_id" in line["extraction_errors"]


# ── report.jsonl ──────────────────────────────────────────────────────────────


def test_report_line_carries_the_review_fields(writer, breaker, tmp_path):
    item, report = breaker
    writer.write(item, {**U8, **report})

    [line] = _lines(tmp_path / "extracted" / "update-8" / "report.jsonl")
    assert line["name"] == "Breaker of Bodies"
    assert line["url"] == URL
    assert line["update_page"] == "Update_8_named_items"
    assert line["template"] == "shield"
    assert line["unmapped_rows"] == report["unmapped_rows"]
    assert line["unclassified_effects"] == report["unclassified_effects"]
    assert line["extraction_errors"] == item.extraction_errors
    assert line["warnings"] == []


def test_report_is_filed_by_the_listing_update_page(writer, breaker, tmp_path):
    item, report = breaker
    writer.write(item, {**U8, **report})
    writer.write(item, {"update_page": "Update_5_named_items", **report})
    writer.write(item, report)

    out = tmp_path / "extracted"
    assert sorted(p.name for p in out.iterdir()) == ["unknown", "update-5", "update-8"]
    assert all(
        len(_lines(out / d / "report.jsonl")) == 1 for d in ("update-5", "update-8")
    )


def test_rewriting_a_page_replaces_its_report_line(src, registry, breaker, tmp_path):
    item, report = breaker
    vest, vest_report = extract(
        (PAGES / "Item_Dark_Ressurectionist_s_Frock_Vest.html").read_text("utf-8"),
        VEST_URL,
        load_config(),
    )
    writer = CatalogWriter(registry, src / "items", tmp_path / "extracted")
    writer.write(item, {**U8, **report})
    writer.write(vest, {**U8, **vest_report})
    writer.write(item, {**U8, **report, "warnings": ["nameless set"]})
    CatalogWriter(registry, src / "items", tmp_path / "extracted").write(
        item, {**U8, **report}
    )

    lines = _lines(tmp_path / "extracted" / "update-8" / "report.jsonl")
    assert [line["url"] for line in lines] == [VEST_URL, URL]
    assert len(_item_files(src)) == 2


def test_two_spellings_of_one_page_url_share_one_file_and_report_line(
    src, writer, breaker, tmp_path
):
    item, report = breaker
    for url in (VEST_URL, VEST_URL.replace("%27", "'")):
        writer.write(_with(item, wiki={"url": url}), {**U8, **report})

    assert len(_item_files(src)) == 1
    assert len(_lines(tmp_path / "extracted" / "update-8" / "report.jsonl")) == 1


# ── check_catalog ─────────────────────────────────────────────────────────────


def test_check_passes_an_empty_or_missing_catalog(src):
    assert check_catalog(src) == []
    (src / "items").mkdir(parents=True)
    (src / "items" / ".gitkeep").write_text("")
    (src / "registry.jsonl").write_text("")
    assert check_catalog(str(src)) == []


@pytest.fixture
def catalog(src, registry, writer, breaker):
    """A sound catalog-src written by CatalogWriter, and its one item file."""
    item, report = breaker
    writer.write(item, {**U8, **report})
    writer.write(
        _with(item, name="Sword", category="weapon", wiki={"page_id": 7}),
        {"update_page": "Update_5_named_items", **report},
    )
    registry.save()
    [path] = (src / "items" / "update-8").rglob("*.json")
    return path


@pytest.mark.usefixtures("catalog")
def test_check_passes_what_the_writer_wrote(src):
    assert check_catalog(src) == []


def _problems(src):
    problems = check_catalog(src)
    assert problems, "expected at least one problem"
    return " | ".join(problems)


@pytest.mark.usefixtures("catalog")
def test_check_flags_an_id_missing_from_the_registry(src):
    (src / "registry.jsonl").write_text("")
    assert "is not in the registry" in _problems(src)


def test_check_flags_a_page_id_that_differs_from_the_registry(src, catalog):
    data = json.loads(catalog.read_text())
    data["wiki"]["page_id"] = 1
    catalog.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    assert "registry page_id 61183 != wiki.page_id 1" in _problems(src)


def test_check_flags_a_second_file_for_one_uuid(src, catalog):
    other = src / "items" / "update-9" / "shield" / catalog.name
    other.parent.mkdir(parents=True)
    shutil.copy(catalog, other)
    assert "2 files share this id" in _problems(src)


def test_check_flags_a_wrong_category_folder(src, catalog):
    moved = src / "items" / "update-8" / "armor" / catalog.name
    moved.parent.mkdir()
    catalog.rename(moved)
    assert "category folder 'armor' should be 'shield'" in _problems(src)


def test_check_flags_a_wrong_slug(src, catalog):
    catalog.rename(catalog.with_name(catalog.name[:37] + "breaker.json"))
    assert "slug 'breaker' should be 'breaker-of-bodies'" in _problems(src)


def test_check_flags_a_bad_update_folder(src, catalog):
    moved = src / "items" / "Update_8" / "shield" / catalog.name
    moved.parent.mkdir(parents=True)
    catalog.rename(moved)
    assert "is not update-<N> or unknown" in _problems(src)


def test_check_flags_a_file_outside_the_layout(src, catalog):
    shutil.copy(catalog, src / "items" / "update-8" / catalog.name)
    assert "not at items/<update>/<category>/<uuid>-<slug>.json" in _problems(src)


def test_check_flags_an_id_that_is_not_the_filename_uuid(src, catalog):
    other_id = "00000000-0000-4000-8000-000000000000"
    catalog.rename(catalog.with_name(other_id + catalog.name[36:]))
    assert "does not match the filename" in _problems(src)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda _text: "{not json", "not {id} + a valid Scraped Item"),
        (
            lambda text: json.dumps({**json.loads(text), "bogus": 1}, indent=2),
            "not {id} + a valid Scraped Item",
        ),
        (
            lambda text: json.dumps(
                {k: v for k, v in json.loads(text).items() if k != "id"}
                | {"id": json.loads(text)["id"]},
                indent=2,
            ),
            '"id" as its first key',
        ),
        (lambda text: text.rstrip("\n"), "canonical form"),
        (lambda text: json.dumps(json.loads(text)) + "\n", "canonical form"),
    ],
)
def test_check_flags_content_that_is_not_id_plus_a_scraped_item(
    src, catalog, mutate, message
):
    catalog.write_text(mutate(catalog.read_text()))
    assert message in _problems(src)


@pytest.mark.usefixtures("catalog")
def test_check_flags_a_malformed_registry(src):
    (src / "registry.jsonl").write_text("not json\n")
    problems = _problems(src)
    assert "registry.jsonl" in problems
