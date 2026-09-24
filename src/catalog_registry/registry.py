"""The UUID registry module: Named Item identity, minted once and kept forever (ADR 0003).

Interface: :meth:`Registry.load`, :meth:`Registry.id_for` and :meth:`Registry.save`.
Behind it the module owns the registry rules:

* the file is JSON Lines, one ``{"id", "page_id", "title"}`` object per line, sorted by
  ``page_id`` so diffs stay small;
* a page is matched by its wiki page ID only. A renamed page keeps its UUID and only the
  stored title changes;
* an entry is never deleted and never given a new UUID. Merges and removals are
  maintainer-curated redirects, which live elsewhere;
* a new page ID gets a fresh ``uuid4``, lowercase and hyphenated.

The file on disk is the only seam. A missing file loads as an empty registry, and
:meth:`Registry.save` creates it.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Union

_UUID4 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


@dataclass
class _Entry:
    id: str
    page_id: int
    title: str


class Registry:
    """The Named Item UUID registry held in one JSON Lines file.

    Build it with :meth:`load`. :meth:`id_for` changes only the in-memory copy; nothing
    reaches the file until :meth:`save`.
    """

    def __init__(self, path: Path, entries: dict[int, _Entry]) -> None:
        self._path = path
        self._entries = entries

    @classmethod
    def load(cls, path: Union[str, os.PathLike]) -> Registry:
        """Read the registry file at ``path``.

        A missing file gives an empty registry, which :meth:`save` writes to ``path``.
        Blank lines are skipped.

        Raises:
            ValueError: A line is not a valid entry (bad JSON, wrong keys or types, an ID
                that is not a lowercase hyphenated uuid4), or two entries share a page ID
                or a UUID. The message names the file and line.
        """
        path = Path(path)
        entries: dict[int, _Entry] = {}
        if not path.exists():
            return cls(path, entries)
        seen_ids: set[str] = set()
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                entry = _parse_line(line, f"{path}:{number}")
                if entry.page_id in entries:
                    raise ValueError(
                        f"{path}:{number}: duplicate page_id {entry.page_id}"
                    )
                if entry.id in seen_ids:
                    raise ValueError(f"{path}:{number}: duplicate id {entry.id}")
                entries[entry.page_id] = entry
                seen_ids.add(entry.id)
        return cls(path, entries)

    def id_for(self, page_id: int, title: str) -> str:
        """Return the UUID for wiki page ``page_id``, minting one if the page is new.

        A known page ID keeps its UUID whatever ``title`` is; the stored title is updated
        to ``title`` (a rename). A new page ID gets a new uuid4.

        Raises:
            TypeError: ``page_id`` is not an ``int`` (``bool`` included), or ``title``
                is not a ``str``.
        """
        if not isinstance(page_id, int) or isinstance(page_id, bool):
            raise TypeError(f"page_id must be an int, got {page_id!r}")
        if not isinstance(title, str):
            raise TypeError(f"title must be a str, got {title!r}")
        entry = self._entries.get(page_id)
        if entry is None:
            entry = _Entry(id=self._mint(), page_id=page_id, title=title)
            self._entries[page_id] = entry
        else:
            entry.title = title
        return entry.id

    def save(self) -> None:
        """Write the registry back to the file it was loaded from, sorted by page ID.

        The write is atomic (a temporary file, then a rename), and parent directories are
        created. The same entries always give the same bytes.
        """
        lines = [
            json.dumps(
                {"id": entry.id, "page_id": entry.page_id, "title": entry.title},
                ensure_ascii=False,
            )
            + "\n"
            for _, entry in sorted(self._entries.items())
        ]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(self._path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines)
        os.replace(temporary, self._path)

    def _mint(self) -> str:
        taken = {entry.id for entry in self._entries.values()}
        while True:
            candidate = str(uuid.uuid4())
            if candidate not in taken:
                return candidate


def _parse_line(line: str, where: str) -> _Entry:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{where}: not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or set(raw) != {"id", "page_id", "title"}:
        raise ValueError(f"{where}: expected exactly the keys id, page_id and title")
    entry_id, page_id, title = raw["id"], raw["page_id"], raw["title"]
    if not isinstance(entry_id, str) or not _UUID4.fullmatch(entry_id):
        raise ValueError(
            f"{where}: id {entry_id!r} is not a lowercase hyphenated uuid4"
        )
    if not isinstance(page_id, int) or isinstance(page_id, bool):
        raise ValueError(f"{where}: page_id {page_id!r} is not an integer")
    if not isinstance(title, str):
        raise ValueError(f"{where}: title {title!r} is not a string")
    return _Entry(id=entry_id, page_id=page_id, title=title)
