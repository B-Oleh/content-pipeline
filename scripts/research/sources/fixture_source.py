"""Deterministic static research source used for local testing and development."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.research.models import (
    ContentPillar,
    ContentRole,
    GamePriceType,
    HardwareTier,
    ReleaseRelevance,
    ResearchCandidate,
)
from scripts.research.sources.base import ResearchSource
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)


class FixtureResearchSource(ResearchSource):
    """Reads candidates from a JSON fixture file.

    Does not depend on network access or third-party services, so it
    produces the same output every run -- useful for tests and for
    exercising the rest of the pipeline before real sources are trusted.
    """

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = Path(path)

    def fetch(self) -> list[ResearchCandidate]:
        raw = self.path.read_text(encoding="utf-8")
        items = json.loads(raw)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        candidates: list[ResearchCandidate] = []
        for index, item in enumerate(items):
            try:
                candidates.append(self._build_candidate(index, item, retrieved_at))
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed fixture candidate at index %d in %s: %s",
                    index,
                    self.path,
                    exc,
                )
        return candidates

    def _build_candidate(self, index: int, item: dict, retrieved_at: str) -> ResearchCandidate:
        raw_metadata = dict(item.get("raw_metadata", {}))
        raw_metadata.setdefault("retrieved_at", retrieved_at)
        return ResearchCandidate(
            candidate_id=item.get("candidate_id") or f"{self.name}:{index}",
            title=item["title"],
            content_pillar=ContentPillar(item["content_pillar"]),
            content_role=ContentRole(item["content_role"]),
            source_name=self.name,
            monetization_path=item.get("monetization_path"),
            source_url=item.get("source_url"),
            summary=item.get("summary"),
            hardware_tier=HardwareTier(item["hardware_tier"]) if item.get("hardware_tier") else None,
            target_gpu_class=item.get("target_gpu_class"),
            game_price_type=GamePriceType(item["game_price_type"]) if item.get("game_price_type") else None,
            release_relevance=ReleaseRelevance(item["release_relevance"])
            if item.get("release_relevance")
            else None,
            game_title=item.get("game_title"),
            raw_metadata=raw_metadata,
        )
