"""Config-driven extraction of DDO wiki item pages into Scraped Items.

The module's interface is ``load_config()`` (rules in ``catalog/extractor/``) and
``extract()``, which returns a :class:`ScrapedItem` and a per-page report.
"""

from item_extractor.config import Config, ConfigError, load_config
from item_extractor.extractor import ExtractionError, extract
from item_extractor.report import aggregate
from item_extractor.scraped_item import ScrapedItem

__all__ = [
    "Config",
    "ConfigError",
    "ExtractionError",
    "ScrapedItem",
    "aggregate",
    "extract",
    "load_config",
]
