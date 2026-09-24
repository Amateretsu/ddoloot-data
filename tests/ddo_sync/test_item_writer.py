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


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_writes_item_json_under_its_update_folder(tmp_path, breaker):
    item, report = breaker
    JsonItemWriter(tmp_path).write(
        item, {"update_page": "Update_8_named_items", **report}
    )

    written = json.loads(
        (tmp_path / "update-8" / "Item_Breaker_of_Bodies.json").read_text()
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
    assert sorted(p.name for p in (tmp_path / "update-8").glob("*.json")) == [
        "Item_Breaker_of_Bodies.json",
        "Item_Dark_Ressurectionist_s_Frock_Vest.json",
    ]


def test_a_page_without_an_update_goes_to_unknown(tmp_path, breaker):
    item, report = breaker
    writer = JsonItemWriter(tmp_path)
    writer.write(item, report)
    writer.write(item, {"update_page": "Update_50_revamped_named_items", **report})

    assert (tmp_path / "unknown" / "Item_Breaker_of_Bodies.json").exists()
    [line] = _lines(tmp_path / "unknown" / "report.jsonl")
    assert line["update_page"] == "Update_50_revamped_named_items"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unknown"]
