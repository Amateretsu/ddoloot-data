"""load_config() at its interface: the shipped config loads; a malformed one fails clearly."""

import shutil

import pytest
import yaml

from item_extractor import Config, ConfigError, load_config
from item_extractor.config import DEFAULT_CONFIG_DIR


@pytest.fixture
def config_dir(tmp_path):
    """A writable copy of the shipped config."""
    dest = tmp_path / "extractor"
    shutil.copytree(DEFAULT_CONFIG_DIR, dest)
    return dest


def edit(config_dir, name, change):
    """Load ``<name>.yaml``, apply *change* to the parsed data, write it back."""
    path = config_dir / f"{name}.yaml"
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data))


def test_shipped_config_loads_as_typed_models():
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.fields.rule_for("minimum level").target == "minimum_level"
    assert cfg.fields.rule_for("made from") is cfg.fields.rule_for("material")
    assert cfg.templates.templates[-1].detect.default is True
    assert cfg.enchantments.rules[-1].fallback is True
    assert cfg.enchantments.roman["V"] == 5


def test_spread_row_without_target_loads(config_dir):
    def drop_source_target(data):
        rule = next(r for r in data["fields"] if r["coerce"] == "location")
        del rule["target"]

    edit(config_dir, "fields", drop_source_target)
    assert load_config(config_dir).fields.rule_for("location").target is None


def test_plain_row_without_target_is_rejected(config_dir):
    edit(config_dir, "fields", lambda d: d["fields"][0].pop("target"))
    with pytest.raises(ConfigError, match="target is required unless spread"):
        load_config(config_dir)


@pytest.mark.parametrize(
    ("name", "change", "where"),
    [
        (
            "fields",
            lambda d: d["fields"][0].update(needs_cell=True),
            "fields.0.needs_cell",
        ),
        ("fields", lambda d: d.update(colour="red"), "colour"),
        (
            "templates",
            lambda d: d["templates"][0]["detect"].update(first=["x"]),
            "first",
        ),
        ("mappings", lambda d: d.update(proficiencies={}), "proficiencies"),
        ("enchantments", lambda d: d["rules"][0].update(when_not={}), "when_not"),
    ],
)
def test_unknown_key_is_rejected_naming_file_and_key(config_dir, name, change, where):
    edit(config_dir, name, change)
    with pytest.raises(ConfigError, match=rf"{name}\.yaml: .*{where}.*not permitted"):
        load_config(config_dir)


def test_target_that_names_no_field_is_rejected(config_dir):
    edit(
        config_dir,
        "fields",
        lambda d: d["fields"][0].update(target="weapon_stats.nonsense"),
    )
    with pytest.raises(
        ConfigError,
        match=r"'weapon_stats\.nonsense' is neither a ScrapedItem field nor a template input",
    ):
        load_config(config_dir)


def test_unknown_coercer_is_rejected(config_dir):
    edit(config_dir, "fields", lambda d: d["fields"][0].update(coerce="nope"))
    with pytest.raises(
        ConfigError, match=r"fields\.0\.coerce: .*unknown coercer 'nope'"
    ):
        load_config(config_dir)


def test_duplicate_label_is_rejected(config_dir):
    edit(
        config_dir,
        "fields",
        lambda d: d["fields"].append(
            {"target": "notes", "labels": ["Material:"], "coerce": "text"}
        ),
    )
    with pytest.raises(ConfigError, match="label 'material' is mapped twice"):
        load_config(config_dir)


@pytest.mark.parametrize(
    ("name", "change"),
    [
        ("enchantments", lambda d: d["rules"][0].update(pattern="(unclosed")),
        ("enchantments", lambda d: d["bonus_type"].update(link_pattern="[a-")),
        ("fields", lambda d: d["ignore"]["label_patterns"].append("(unclosed")),
    ],
)
def test_bad_regex_is_rejected(config_dir, name, change):
    edit(config_dir, name, change)
    with pytest.raises(ConfigError, match=rf"{name}\.yaml: .*bad pattern"):
        load_config(config_dir)


def test_set_rule_without_item_pattern_is_rejected(config_dir):
    def drop(data):
        next(r for r in data["rules"] if r["kind"] == "set").pop("item_pattern")

    edit(config_dir, "enchantments", drop)
    with pytest.raises(ConfigError, match="a set rule needs item_pattern"):
        load_config(config_dir)


def test_last_template_must_be_the_default(config_dir):
    edit(config_dir, "templates", lambda d: d["templates"].reverse())
    with pytest.raises(ConfigError, match="the last template must be the default"):
        load_config(config_dir)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="missing config file"):
        load_config(tmp_path)
