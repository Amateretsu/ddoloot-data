"""Config-driven extraction of DDO wiki item pages (rules live in catalog/normalizer/)."""

from item_extractor.config import Config, ConfigError, load_config
from item_extractor.extractor import ExtractionError, extract
from item_extractor.report import aggregate

__all__ = [
    "Config",
    "ConfigError",
    "ExtractionError",
    "aggregate",
    "extract",
    "load_config",
]
