"""Load the extractor config (``catalog/extractor/*.yaml``) as typed models.

``load_config()`` is this module's interface. It parses each of the four YAML files into a
pydantic model with ``extra="forbid"``, so a misspelt or unknown key fails at load rather
than being silently ignored, and it runs the cross-file checks (every ``fields.yaml``
target names a ScrapedItem field or a template input). Regex strings are compiled at load;
a bad pattern is a load error. Everything the extractor reads from the config is an
attribute of the returned :class:`Config`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    model_validator,
)

from item_extractor.coercers import COERCERS
from item_extractor.scraped_item import has_field

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "catalog" / "extractor"


class ConfigError(ValueError):
    """The extractor config is missing or malformed."""


def normalize_label(text: str) -> str:
    """Lowercase, collapse whitespace (including NBSP), and drop a trailing colon."""
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text.rstrip(":").rstrip().lower()


def _compiler(flags: int = 0):
    def compile_(value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return re.compile(value, flags)
        except re.error as exc:
            raise ValueError(f"bad pattern {value!r}: {exc}") from exc

    return compile_


def _known_coercer(name: str) -> str:
    if name not in COERCERS:
        raise ValueError(f"unknown coercer {name!r}")
    return name


Regex = Annotated[re.Pattern[str], BeforeValidator(_compiler())]
#: A row-label pattern: matched case-insensitively.
LabelRegex = Annotated[re.Pattern[str], BeforeValidator(_compiler(re.I))]
#: A row label as written in the YAML, stored normalised (see :func:`normalize_label`).
Label = Annotated[str, AfterValidator(normalize_label)]
Coercer = Annotated[str, AfterValidator(_known_coercer)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── fields.yaml ───────────────────────────────────────────────────────────────


class FieldRule(_Strict):
    """One infobox row mapping: which labels, which coercer, which ScrapedItem field."""

    labels: list[Label] = Field(min_length=1)
    coerce: Coercer
    target: str | None = None
    spread: bool = False

    @model_validator(mode="after")
    def _target_unless_spread(self) -> FieldRule:
        if self.target is None and not self.spread:
            raise ValueError(
                f"row {self.labels[0]!r}: target is required unless spread"
            )
        return self

    @property
    def error_key(self) -> str:
        """The ``extraction_errors`` key when the coercer cannot read the row."""
        return self.target or self.labels[0]


class IgnoreRules(_Strict):
    labels: frozenset[Label] = frozenset()
    label_patterns: tuple[LabelRegex, ...] = ()


class FieldsConfig(_Strict):
    """``fields.yaml``: row labels -> ScrapedItem fields."""

    null_values: frozenset[Annotated[str, AfterValidator(str.lower)]]
    fields: list[FieldRule]
    ignore: IgnoreRules = IgnoreRules()
    effects_labels: frozenset[Label]

    _by_label: dict[str, FieldRule] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _index_labels(self) -> FieldsConfig:
        by_label: dict[str, FieldRule] = {}
        for rule in self.fields:
            for label in rule.labels:
                if label in by_label:
                    raise ValueError(f"label {label!r} is mapped twice")
                by_label[label] = rule
        self._by_label = by_label
        return self

    def rule_for(self, label: str) -> FieldRule | None:
        """The rule for a normalised row label, if one maps it."""
        return self._by_label.get(label)

    def is_ignored(self, label: str) -> bool:
        """Whether a normalised row label is recognised but deliberately not stored."""
        return label in self.ignore.labels or any(
            p.search(label) for p in self.ignore.label_patterns
        )


# ── mappings.yaml ─────────────────────────────────────────────────────────────


class MappingsConfig(_Strict):
    """``mappings.yaml``: value maps the coercers apply."""

    binding: dict[str, str]
    damage_types: dict[str, str]
    denominations: dict[str, int]


# ── templates.yaml ────────────────────────────────────────────────────────────


class Detect(_Strict):
    first_label: frozenset[Label] = frozenset()
    has_label: frozenset[Label] = frozenset()
    default: bool = False


class SplitPart(_Strict):
    """Take part ``index`` of ``field`` split on every separator in ``split``."""

    field: str
    split: list[str]
    index: int


class SplitAll(_Strict):
    """Every part of ``field`` split on every separator in ``split``."""

    field: str
    split: list[str]


class Template(_Strict):
    id: str
    detect: Detect
    category: str | None = None
    category_from: SplitPart | None = None
    item_type_from: str | None = None
    item_type_from_split: SplitPart | None = None
    equip_slots: list[str] = []
    equip_slots_from: SplitAll | None = None


class TemplatesConfig(_Strict):
    """``templates.yaml``: page templates in match order, and the category map."""

    templates: list[Template] = Field(min_length=1)
    category_map: dict[str, str]

    @model_validator(mode="after")
    def _default_last(self) -> TemplatesConfig:
        if not self.templates[-1].detect.default:
            raise ValueError("the last template must be the default")
        return self


# ── enchantments.yaml ─────────────────────────────────────────────────────────


class BonusTypePatterns(_Strict):
    link_pattern: Regex
    text_pattern: Regex
    tooltip_pattern: Regex


class When(_Strict):
    has_children: bool | None = None
    tooltip_items_match: Regex | None = None


class EntryRule(_Strict):
    """One classification rule for an Enchantments/Enhancements list entry."""

    id: str
    kind: Literal["effect", "set", "set_bonus", "hint"]
    pattern: Regex
    when: When = When()
    item_pattern: Regex | None = None
    bonus_type_from: Literal["text"] | None = None
    hint_kind: str | None = None
    value_kind: Literal["flat", "percent", "tier", "number"] | None = None
    lowercase: list[str] = []
    fallback: bool = False

    @model_validator(mode="after")
    def _kind_needs(self) -> EntryRule:
        if self.kind == "set" and self.item_pattern is None:
            raise ValueError(f"rule {self.id!r}: a set rule needs item_pattern")
        if self.kind == "hint" and self.hint_kind is None:
            raise ValueError(f"rule {self.id!r}: a hint rule needs hint_kind")
        return self


class EnchantmentsConfig(_Strict):
    """``enchantments.yaml``: everything Effect classification reads.

    Bonus Type patterns, the Roman numeral map for tier values, and the ordered entry
    rules. It is the whole rules argument of ``classify_effects()``.
    """

    bonus_type: BonusTypePatterns
    roman: dict[str, int]
    rules: list[EntryRule] = Field(min_length=1)


# ── The whole config ──────────────────────────────────────────────────────────


class Config(_Strict):
    """The four extractor YAML files, typed and cross-checked."""

    fields: FieldsConfig
    templates: TemplatesConfig
    mappings: MappingsConfig
    enchantments: EnchantmentsConfig

    _template_inputs: frozenset[str] = PrivateAttr(default=frozenset())

    @model_validator(mode="after")
    def _targets_exist(self) -> Config:
        self._template_inputs = frozenset(
            src.field
            for tpl in self.templates.templates
            for src in (
                tpl.category_from,
                tpl.item_type_from_split,
                tpl.equip_slots_from,
            )
            if src is not None and not has_field(src.field)
        )
        for rule in self.fields.fields:
            target = rule.target
            if target is not None and not (
                has_field(target) or target in self._template_inputs
            ):
                raise ValueError(
                    f"target {target!r} is neither a ScrapedItem field nor a template input"
                )
        return self

    @property
    def template_inputs(self) -> frozenset[str]:
        """Fields rows fill only for a template to consume (e.g. ``slot``)."""
        return self._template_inputs


_FILES: dict[str, type[_Strict]] = {
    "fields": FieldsConfig,
    "templates": TemplatesConfig,
    "mappings": MappingsConfig,
    "enchantments": EnchantmentsConfig,
}


def load_config(config_dir: Path | None = None) -> Config:
    """Read and validate the four YAML files in *config_dir* (default ``catalog/extractor``).

    Raises:
        ConfigError: a file is missing or not YAML; a key is unknown or missing; a coercer
            is unknown; a label is mapped twice; a regex does not compile; a target is
            neither a ScrapedItem field nor a template input; or the last template is not
            the default. The message names the file and the offending key.
    """
    config_dir = config_dir or DEFAULT_CONFIG_DIR
    parts: dict[str, _Strict] = {}
    for name, model in _FILES.items():
        path = config_dir / f"{name}.yaml"
        if not path.exists():
            raise ConfigError(f"missing config file: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            parts[name] = model.model_validate(raw)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: not YAML: {exc}") from exc
        except ValidationError as exc:
            raise ConfigError(f"{path}: {_describe(exc)}") from exc
    try:
        return Config(**parts)
    except ValidationError as exc:
        raise ConfigError(f"{config_dir}: {_describe(exc)}") from exc


def _describe(exc: ValidationError) -> str:
    """One ``key.path: message`` line per error, without pydantic's input dumps."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '(top level)'}: {err['msg']}"
        for err in exc.errors()
    )
