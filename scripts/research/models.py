"""Data models for Research Agent V0.1.

All score dimensions use a documented 0-10 scale, where 0 is worst/none and
10 is best/most. See docs/RESEARCH_AGENT.md for the full scoring and ranking
design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

MIN_SCORE = 0.0
MAX_SCORE = 10.0

SCORE_DIMENSIONS: tuple[str, ...] = (
    "audience_interest",
    "commercial_intent",
    "affiliate_potential",
    "competition",
    "novelty",
    "visual_potential",
    "retention_potential",
    "production_difficulty",
    "confidence",
    "freshness",
    "evergreen_value",
)


class ContentRole(str, Enum):
    """Primary purpose of a piece of content (see CLAUDE.md "Business signals")."""

    GROWTH = "growth"
    REVENUE = "revenue"
    HYBRID = "hybrid"


class ContentPillar(str, Enum):
    """Recurring content pillars supported by the business strategy."""

    BUYING_ADVICE = "buying_advice"
    HARDWARE_COMPARISON = "hardware_comparison"
    OPTIMIZATION = "optimization"
    GAMING_TECHNOLOGY = "gaming_technology"
    MISTAKES_AND_MYTHS = "mistakes_and_myths"
    GAME_RECOMMENDATIONS = "game_recommendations"
    MONTHLY_GAMES = "monthly_games"


class HardwareTier(str, Enum):
    """PC performance tier for the "What to play this month" pillar."""

    LOW_END = "low_end"
    MID_RANGE = "mid_range"
    HIGH_END = "high_end"


class GamePriceType(str, Enum):
    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"


class ReleaseRelevance(str, Enum):
    NEW_RELEASE = "new_release"
    RECENT = "recent"
    EVERGREEN = "evergreen"
    UNKNOWN = "unknown"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ResearchCandidate:
    """A single candidate content opportunity, before scoring."""

    candidate_id: str
    title: str
    content_pillar: ContentPillar
    content_role: ContentRole
    source_name: str
    monetization_path: Optional[str] = None
    source_url: Optional[str] = None
    summary: Optional[str] = None
    discovered_at: datetime = field(default_factory=_utcnow)

    # Optional fields for the "What to play this month" pillar. A candidate
    # in this pillar represents one game recommendation for one hardware
    # tier; a full monthly video groups several such candidates later
    # (Script Agent, not implemented yet).
    hardware_tier: Optional[HardwareTier] = None
    target_gpu_class: Optional[str] = None
    game_price_type: Optional[GamePriceType] = None
    release_relevance: Optional[ReleaseRelevance] = None
    game_title: Optional[str] = None

    # Free-form provenance/hints (e.g. RSS pubDate, future real-data
    # overrides under "known_scores"). Never used to fabricate claims.
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.candidate_id.strip():
            raise ValueError("ResearchCandidate.candidate_id must not be empty")
        if not self.title or not self.title.strip():
            raise ValueError("ResearchCandidate.title must not be empty")
        if not self.source_name or not self.source_name.strip():
            raise ValueError("ResearchCandidate.source_name must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "content_pillar": self.content_pillar.value,
            "content_role": self.content_role.value,
            "source_name": self.source_name,
            "monetization_path": self.monetization_path,
            "source_url": self.source_url,
            "summary": self.summary,
            "discovered_at": self.discovered_at.isoformat(),
            "hardware_tier": self.hardware_tier.value if self.hardware_tier else None,
            "target_gpu_class": self.target_gpu_class,
            "game_price_type": self.game_price_type.value if self.game_price_type else None,
            "release_relevance": self.release_relevance.value if self.release_relevance else None,
            "game_title": self.game_title,
            "raw_metadata": self.raw_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchCandidate":
        discovered_at_raw = data.get("discovered_at")
        discovered_at = (
            datetime.fromisoformat(discovered_at_raw) if discovered_at_raw else _utcnow()
        )
        return cls(
            candidate_id=data["candidate_id"],
            title=data["title"],
            content_pillar=ContentPillar(data["content_pillar"]),
            content_role=ContentRole(data["content_role"]),
            source_name=data["source_name"],
            monetization_path=data.get("monetization_path"),
            source_url=data.get("source_url"),
            summary=data.get("summary"),
            discovered_at=discovered_at,
            hardware_tier=HardwareTier(data["hardware_tier"]) if data.get("hardware_tier") else None,
            target_gpu_class=data.get("target_gpu_class"),
            game_price_type=GamePriceType(data["game_price_type"]) if data.get("game_price_type") else None,
            release_relevance=ReleaseRelevance(data["release_relevance"])
            if data.get("release_relevance")
            else None,
            game_title=data.get("game_title"),
            raw_metadata=data.get("raw_metadata", {}),
        )


@dataclass
class ScoreBreakdown:
    """Score for every dimension in SCORE_DIMENSIONS, each on a 0-10 scale.

    heuristic_dimensions lists which of these values are heuristic guesses
    rather than real evidence (see docs/RESEARCH_AGENT.md "Heuristic vs real
    data"). In V0.1, this is effectively all dimensions, since no analytics
    or search-volume integration exists yet.
    """

    audience_interest: float
    commercial_intent: float
    affiliate_potential: float
    competition: float
    novelty: float
    visual_potential: float
    retention_potential: float
    production_difficulty: float
    confidence: float
    freshness: float
    evergreen_value: float
    heuristic_dimensions: frozenset[str] = field(default_factory=lambda: frozenset(SCORE_DIMENSIONS))

    def __post_init__(self) -> None:
        for dimension in SCORE_DIMENSIONS:
            value = getattr(self, dimension)
            if not isinstance(value, (int, float)):
                raise ValueError(f"ScoreBreakdown.{dimension} must be numeric, got {value!r}")
            if not (MIN_SCORE <= value <= MAX_SCORE):
                raise ValueError(
                    f"ScoreBreakdown.{dimension} must be between {MIN_SCORE} and {MAX_SCORE}, got {value}"
                )
        unknown = set(self.heuristic_dimensions) - set(SCORE_DIMENSIONS)
        if unknown:
            raise ValueError(f"ScoreBreakdown.heuristic_dimensions has unknown dimension(s): {unknown}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {dim: getattr(self, dim) for dim in SCORE_DIMENSIONS}
        data["heuristic_dimensions"] = sorted(self.heuristic_dimensions)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScoreBreakdown":
        kwargs = {dim: data[dim] for dim in SCORE_DIMENSIONS}
        kwargs["heuristic_dimensions"] = frozenset(data.get("heuristic_dimensions", SCORE_DIMENSIONS))
        return cls(**kwargs)


@dataclass
class ScoredCandidate:
    """A candidate combined with its score breakdown and ranking outcome."""

    candidate: ResearchCandidate
    scores: ScoreBreakdown
    overall_score: float
    rank: Optional[int] = None
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "scores": self.scores.to_dict(),
            "overall_score": self.overall_score,
            "rank": self.rank,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScoredCandidate":
        return cls(
            candidate=ResearchCandidate.from_dict(data["candidate"]),
            scores=ScoreBreakdown.from_dict(data["scores"]),
            overall_score=data["overall_score"],
            rank=data.get("rank"),
            reasoning=data.get("reasoning", []),
        )


@dataclass
class ResearchResult:
    """Top-level persisted output of one Research Agent run."""

    generated_at: datetime
    ranking_formula_version: str
    candidates: list[ScoredCandidate]
    source_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "ranking_formula_version": self.ranking_formula_version,
            "candidates": [sc.to_dict() for sc in self.candidates],
            "source_errors": self.source_errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchResult":
        return cls(
            generated_at=datetime.fromisoformat(data["generated_at"]),
            ranking_formula_version=data["ranking_formula_version"],
            candidates=[ScoredCandidate.from_dict(sc) for sc in data.get("candidates", [])],
            source_errors=data.get("source_errors", []),
        )
