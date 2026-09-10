"""Generic JSON-file persistence primitive for state that must survive
across Research Agent runs (see docs/RESEARCH_AGENT.md "Persistent state").

This is deliberately not a database: it is the simplest reasonable
persistence boundary for V0.2 -- one class with load/save/append/extend,
backed by a single JSON file holding a list of plain dicts. Every
persistent-state module in this package (currently game_history.py) is
built on top of this class instead of reading/writing JSON directly, so the
backing store can be swapped for something else later (e.g. SQLite, a small
managed database) by reimplementing this one class without changing any
caller.

Writes go through scripts.utils.atomic_write so an interrupted write (crash,
kill, power loss) can never leave this file truncated/corrupted -- a reader
always sees either the complete previous content or the complete new
content. See that module's docstring for what it does and does not
guarantee (in particular: not safe against concurrent writers).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.utils.atomic_write import atomic_write_text


class JsonListStore:
    """Persists a list of plain (JSON-serializable) dicts to one JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, items: list[dict[str, Any]]) -> None:
        atomic_write_text(self.path, json.dumps(items, indent=2, ensure_ascii=False))

    def append(self, item: dict[str, Any]) -> None:
        self.extend([item])

    def extend(self, new_items: list[dict[str, Any]]) -> None:
        if not new_items:
            return
        items = self.load()
        items.extend(new_items)
        self.save(items)
