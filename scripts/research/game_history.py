"""Persisted history of previously recommended games.

Exists so a future version of the "What to play this month" pillar can
deliberately balance new releases, hidden gems, free games, and AAA titles
instead of repeating the same games every month (see CLAUDE.md "Game
recommendation integrity"). V0.1 only records history; it does not yet use
it to influence selection -- that is a V0.2+ concern (see
docs/RESEARCH_AGENT.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.research.models import ResearchResult

HISTORY_FILENAME = "game_history.json"


def _load_raw(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_game_history(base_dir: Path) -> list[dict[str, Any]]:
    """Return every previously recorded game recommendation entry."""
    return _load_raw(Path(base_dir) / HISTORY_FILENAME)


def record_game_recommendations(result: ResearchResult, base_dir: Path) -> Path:
    """Append one entry per monthly-games candidate with a game_title.

    Entries are plain data (candidate_id, game_title, hardware_tier,
    game_price_type, recommended_at) so future selection logic can query
    history without depending on this module's internals.
    """
    path = Path(base_dir) / HISTORY_FILENAME
    history = _load_raw(path)

    for scored in result.candidates:
        candidate = scored.candidate
        if not candidate.game_title:
            continue
        history.append(
            {
                "candidate_id": candidate.candidate_id,
                "game_title": candidate.game_title,
                "hardware_tier": candidate.hardware_tier.value if candidate.hardware_tier else None,
                "game_price_type": candidate.game_price_type.value if candidate.game_price_type else None,
                "content_pillar": candidate.content_pillar.value,
                "recommended_at": result.generated_at.isoformat(),
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
