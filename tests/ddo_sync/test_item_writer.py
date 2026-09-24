"""JsonItemWriter, through its write() interface."""

import json

import pytest

from ddo_sync import JsonItemWriter
from item_extractor import ScrapedItem, extract, load_config
from tests.ddo_sync.conftest import ITEM_PAGE_HTML, PAGES

URL = "https://ddowiki.com/page/Item:Breaker_of_Bodies"
VEST_URL = "https://ddowiki.com/page/Item:Dark_Ressurectionist%27s_Frock_Vest"


@pytest.fixture(scope="module")
def breaker():
    return extract(ITEM_PAGE_HTML, URL, load_config())


def _one(folder, stem):
    """The JSON file for *stem*; names carry an 8-hex title hash after the stem."""
    [path] = folder.glob(f"{stem}-????????.json")
    return path


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_writes_item_json_under_its_update_folder(tmp_path, breaker):
    item, report = breaker
    JsonItemWriter(tmp_path).write(
        item, {"update_page": "Update_8_named_items", **report}
    )

    written = json.loads(
        _one(tmp_path / "update-8", "Item_Breaker_of_Bodies").read_text()
    )
    assert set(written) == set(ScrapedItem.model_fields), "every field is written"
    assert ScrapedItem.model_validate(written) == item


def test_report_line_carries_the_review_fields(tmp_path, breaker):
    item, report = breaker
    JsonItemWriter(tmp_path).write(
        item, {"update_page": "Update_8_named_items", **report}
    )

    [line] = _lines(tmp_path / "update-8" / "report.jsonl")
    assert line["name"] == "Breaker of Bodies"
    assert line["url"] == URL
    assert line["update_page"] == "Update_8_named_items"
    assert line["template"] == "shield"
    assert line["unmapped_rows"] == report["unmapped_rows"]
    assert line["unclassified_effects"] == report["unclassified_effects"]
    assert line["extraction_errors"] == item.extraction_errors
    assert line["warnings"] == []


def test_rewriting_a_page_replaces_its_report_line(tmp_path, breaker):
    item, report = breaker
    vest, vest_report = extract(
        (PAGES / "Item_Dark_Ressurectionist_s_Frock_Vest.html").read_text("utf-8"),
        VEST_URL,
        load_config(),
    )
    writer = JsonItemWriter(tmp_path)
    tagged = {"update_page": "Update_8_named_items"}
    writer.write(item, {**tagged, **report})
    writer.write(vest, {**tagged, **vest_report})
    writer.write(item, {**tagged, **report, "warnings": ["nameless set"]})
    JsonItemWriter(tmp_path).write(item, {**tagged, **report})

    lines = _lines(tmp_path / "update-8" / "report.jsonl")
    assert [line["url"] for line in lines] == [VEST_URL, URL]
    assert sorted(p.stem[:-9] for p in (tmp_path / "update-8").glob("*.json")) == [
        "Item_Breaker_of_Bodies",
        "Item_Dark_Ressurectionist_s_Frock_Vest",
    ]


def test_a_page_without_an_update_goes_to_unknown(tmp_path, breaker):
    item, report = breaker
    writer = JsonItemWriter(tmp_path)
    writer.write(item, report)
    writer.write(item, {"update_page": "Update_50_revamped_named_items", **report})

    assert _one(tmp_path / "unknown", "Item_Breaker_of_Bodies").exists()
    [line] = _lines(tmp_path / "unknown" / "report.jsonl")
    assert line["update_page"] == "Update_50_revamped_named_items"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unknown"]


def _as_page(item, url):
    return item.model_copy(update={"wiki": item.wiki.model_copy(update={"url": url})})


def test_titles_differing_only_in_punctuation_or_case_get_their_own_files(
    tmp_path, breaker
):
    item, report = breaker
    urls = [
        "https://ddowiki.com/page/Item:Foo%27s_Bar",
        "https://ddowiki.com/page/Item:Foo_s_Bar",
        "https://ddowiki.com/page/Item:Firebreak_(level_17)",
        "https://ddowiki.com/page/Item:Firebreak_(Level_17)",
    ]
    writer = JsonItemWriter(tmp_path)
    for url in urls:
        writer.write(
            _as_page(item, url), {"update_page": "Update_8_named_items", **report}
        )

    written = [
        json.loads(p.read_text()) for p in (tmp_path / "update-8").glob("*.json")
    ]
    assert sorted(w["wiki"]["url"] for w in written) == sorted(urls)
    assert len(_lines(tmp_path / "update-8" / "report.jsonl")) == 4


def test_two_spellings_of_one_page_url_share_one_file_and_report_line(
    tmp_path, breaker
):
    item, report = breaker
    writer = JsonItemWriter(tmp_path)
    for url in (VEST_URL, VEST_URL.replace("%27", "'")):
        writer.write(
            _as_page(item, url), {"update_page": "Update_8_named_items", **report}
        )

    assert len(list((tmp_path / "update-8").glob("*.json"))) == 1
    assert len(_lines(tmp_path / "update-8" / "report.jsonl")) == 1
