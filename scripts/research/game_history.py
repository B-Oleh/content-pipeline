"""Persisted history of previously recommended games.

Exists so the "What to play this month" pillar can deliberately balance new
releases, hidden gems, free games, and AAA titles instead of repeating the
same games every month (see CLAUDE.md "Game recommendation integrity"). This
module both records history (every run) and, since V0.2, exposes it so
ranking.py can compute a repetition penalty (see docs/RESEARCH_AGENT.md
"Game history repetition").

Built on scripts.research.state.store.JsonListStore -- the persisted format
is a plain JSON list of
{candidate_id, game_title, hardware_tier, game_price_type, content_pillar,
recommended_at} dicts, so it can be read by any future tool without
depending on this module's internals.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scripts.research.models import ResearchResult
from scripts.research.state.store import JsonListStore

HISTORY_FILENAME = "game_history.json"


def _store(base_dir: Path) -> JsonListStore:
    return JsonListStore(Path(base_dir) / HISTORY_FILENAME)


def load_game_history(base_dir: Path) -> list[dict[str, Any]]:
    """Return every previously recorded game recommendation entry."""
    return _store(base_dir).load()


def record_game_recommendations(result: ResearchResult, base_dir: Path) -> Path:
    """Append one entry per monthly-games candidate with a game_title."""
    store = _store(base_dir)
    new_entries = []
    for scored in result.candidates:
        candidate = scored.candidate
        if not candidate.game_title:
            continue
        new_entries.append(
            {
                "candidate_id": candidate.candidate_id,
                "game_title": candidate.game_title,
                "hardware_tier": candidate.hardware_tier.value if candidate.hardware_tier else None,
                "game_price_type": candidate.game_price_type.value if candidate.game_price_type else None,
                "content_pillar": candidate.content_pillar.value,
                "recommended_at": result.generated_at.isoformat(),
            }
        )
    store.extend(new_entries)
    return store.path


def get_previous_recommendations(game_title: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All history entries for a game title, matched case-insensitively."""
    normalized = game_title.strip().lower()
    return [entry for entry in history if str(entry.get("game_title") or "").strip().lower() == normalized]


def days_since(entries: list[dict[str, Any]], as_of: datetime) -> Optional[float]:
    """Days between as_of and the most recent recommended_at among entries.

    Entries with a missing or malformed recommended_at are skipped rather
    than raising -- persisted state should degrade gracefully, not crash the
    pipeline (see CLAUDE.md "Stage scripts should fail loudly ... rather
    than silently producing partial/incorrect output" -- this is the
    exception for state written by an earlier version or edited by hand,
    which is handled the same way malformed source input is elsewhere in
    this package: skip and continue).
    """
    timestamps = []
    for entry in entries:
        raw = entry.get("recommended_at")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        timestamps.append(parsed)
    if not timestamps:
        return None
    most_recent = max(timestamps)
    return (as_of - most_recent).total_seconds() / 86400
