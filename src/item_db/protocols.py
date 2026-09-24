"""Structural protocols (PEP 544) for item_db dependencies.

Defines the minimal interface that an item repository must satisfy, enabling
loose coupling and easier unit testing via duck-typing.

Example:
    >>> from item_db.protocols import ItemRepositoryProtocol
    >>> def process(repo: ItemRepositoryProtocol) -> None:
    ...     repo.upsert(item)
"""

from __future__ import annotations

from typing import List, Optional, Tuple

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from item_db._filters import ItemFilter
from item_normalizer.models import DDOItem


@runtime_checkable
class ItemRepositoryProtocol(Protocol):
    """Minimal interface for persisting and querying DDOItem objects."""

    def save(self, item: DDOItem) -> int: ...

    def upsert(self, item: DDOItem) -> int: ...

    def delete(self, name: str) -> None: ...

    def save_many(
        self, items: List[DDOItem]
    ) -> Tuple[int, List[Tuple[DDOItem, Exception]]]: ...

    def get(self, name: str) -> DDOItem: ...

    def get_or_none(self, name: str) -> Optional[DDOItem]: ...

    def get_by_id(self, item_id: int) -> DDOItem: ...

    def exists(self, name: str) -> bool: ...

    def count(self) -> int: ...

    def list_names(self) -> List[str]: ...

    def search(self, filters: ItemFilter) -> List[DDOItem]: ...
