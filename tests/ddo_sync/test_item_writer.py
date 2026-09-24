"""JsonItemWriter, through its write() interface."""

import json

from ddo_sync import JsonItemWriter
from item_extractor import ScrapedItem, extract, load_config
from tests.ddo_sync.conftest import ITEM_PAGE_HTML

URL = "https://ddowiki.com/page/Item:Breaker_of_Bodies"


def test_writes_item_json_and_appends_report_line(tmp_path):
    item, report = extract(ITEM_PAGE_HTML, URL, load_config())
    writer = JsonItemWriter(tmp_path / "out")

    writer.write(item, report)
    writer.write(item, report)

    written = json.loads((tmp_path / "out" / "Item_Breaker_of_Bodies.json").read_text())
    assert set(written) == set(ScrapedItem.model_fields), "every field is written"
    assert ScrapedItem.model_validate(written) == item
    lines = [
        json.loads(x)
        for x in (tmp_path / "out" / "report.jsonl").read_text().splitlines()
    ]
    assert len(lines) == 2
    assert lines[0]["name"] == "Breaker of Bodies"
    assert lines[0]["url"] == URL
    assert lines[0]["template"] == "shield"
