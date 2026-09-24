"""catalog_registry: the Named Item UUID registry module (ADR 0003).

The registry is the source of truth for Named Item identity. It maps each wiki page ID to
a UUID that is minted once and never changes, and it lives in the committed file
``catalog-src/registry.jsonl``.

Interface::

    from catalog_registry import Registry

    registry = Registry.load("catalog-src/registry.jsonl")
    named_item_id = registry.id_for(page_id=12387, title="Item:Boots of Corrosion")
    registry.save()

The file on disk is the only seam; there is no adapter to swap.
"""

from catalog_registry.registry import Registry

__all__ = ["Registry"]
