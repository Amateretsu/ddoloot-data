import shutil

import pytest
import yaml

from item_extractor import ConfigError, load_config
from item_extractor.config import DEFAULT_CONFIG_DIR, normalize_label


def test_shipped_config_loads(cfg):
    assert cfg.label_index["minimum level"]["target"] == "minimum_level"
    assert cfg.label_index["made from"] is cfg.label_index["material"]


def test_normalize_label_handles_nbsp_and_colons():
    assert (
        normalize_label("Race\xa0Absolutely   Required: ") == "race absolutely required"
    )


def _copy_config(tmp_path):
    dest = tmp_path / "normalizer"
    shutil.copytree(DEFAULT_CONFIG_DIR, dest)
    return dest


def test_duplicate_label_is_rejected(tmp_path):
    dest = _copy_config(tmp_path)
    data = yaml.safe_load((dest / "fields.yaml").read_text())
    data["fields"].append({"target": "x", "labels": ["Material"], "coerce": "text"})
    (dest / "fields.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="mapped twice"):
        load_config(dest)


def test_unknown_coercer_is_rejected(tmp_path):
    dest = _copy_config(tmp_path)
    data = yaml.safe_load((dest / "fields.yaml").read_text())
    data["fields"][0]["coerce"] = "nope"
    (dest / "fields.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="unknown coercer"):
        load_config(dest)


def test_bad_rule_pattern_is_rejected(tmp_path):
    dest = _copy_config(tmp_path)
    data = yaml.safe_load((dest / "enchantments.yaml").read_text())
    data["rules"][0]["pattern"] = "(unclosed"
    (dest / "enchantments.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="bad pattern"):
        load_config(dest)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="missing config file"):
        load_config(tmp_path)
