"""Heuristic scoring engine for Research Agent V0.1.

No analytics, search-volume, or competition-data integration exists yet, so
every dimension below is computed from simple, documented heuristics over
the candidate's own fields (content pillar, content role, monetization
path, title keywords, and source freshness) rather than real audience or
market data. Every computed dimension is marked heuristic on the returned
ScoreBreakdown unless a candidate's raw_metadata already carries a real
value under "known_scores" -- a deliberate hook for future real-data
providers (see docs/RESEARCH_AGENT.md "Heuristic vs real data").

This module never fabricates search volume, competition numbers, FPS
benchmarks, sales numbers, or popularity statistics (see CLAUDE.md
"Research and opportunity scoring rules" and "Fact checking").

All scores use a 0-10 scale: 0 = worst/none, 10 = best/most.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scripts.research.models import (
    SCORE_DIMENSIONS,
    ContentPillar,
    ContentRole,
    ResearchCandidate,
    ScoreBreakdown,
)
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Baseline heuristics per content pillar. These reflect editorial judgment
# about the niche (see docs/BUSINESS_STRATEGY.md), not measured data.
_PILLAR_AUDIENCE_INTEREST: dict[ContentPillar, float] = {
    ContentPillar.BUYING_ADVICE: 7.0,
    ContentPillar.HARDWARE_COMPARISON: 7.5,
    ContentPillar.OPTIMIZATION: 6.5,
    ContentPillar.GAMING_TECHNOLOGY: 6.0,
    ContentPillar.MISTAKES_AND_MYTHS: 6.5,
    ContentPillar.GAME_RECOMMENDATIONS: 7.0,
    ContentPillar.MONTHLY_GAMES: 6.5,
}

_PILLAR_VISUAL_POTENTIAL: dict[ContentPillar, float] = {
    ContentPillar.BUYING_ADVICE: 6.0,
    ContentPillar.HARDWARE_COMPARISON: 7.0,
    ContentPillar.OPTIMIZATION: 5.0,
    ContentPillar.GAMING_TECHNOLOGY: 6.0,
    ContentPillar.MISTAKES_AND_MYTHS: 5.0,
    ContentPillar.GAME_RECOMMENDATIONS: 8.0,
    ContentPillar.MONTHLY_GAMES: 8.0,
}

_PILLAR_PRODUCTION_DIFFICULTY: dict[ContentPillar, float] = {
    ContentPillar.BUYING_ADVICE: 6.0,
    ContentPillar.HARDWARE_COMPARISON: 6.5,
    ContentPillar.OPTIMIZATION: 5.0,
    ContentPillar.GAMING_TECHNOLOGY: 5.5,
    ContentPillar.MISTAKES_AND_MYTHS: 4.5,
    ContentPillar.GAME_RECOMMENDATIONS: 5.0,
    ContentPillar.MONTHLY_GAMES: 5.5,
}

_PILLAR_EVERGREEN_VALUE: dict[ContentPillar, float] = {
    ContentPillar.BUYING_ADVICE: 6.0,
    ContentPillar.HARDWARE_COMPARISON: 5.0,
    ContentPillar.OPTIMIZATION: 7.0,
    ContentPillar.GAMING_TECHNOLOGY: 5.0,
    ContentPillar.MISTAKES_AND_MYTHS: 7.5,
    ContentPillar.GAME_RECOMMENDATIONS: 4.5,
    ContentPillar.MONTHLY_GAMES: 3.0,  # explicitly time-bound by design
}

_ROLE_COMMERCIAL_INTENT: dict[ContentRole, float] = {
    ContentRole.GROWTH: 2.5,
    ContentRole.HYBRID: 6.0,
    ContentRole.REVENUE: 8.5,
}

_NOVELTY_KEYWORDS = ("new", "2026", "just released", "myth", "surprising", "hidden gem")
_RETENTION_KEYWORDS = ("vs", "mistake", "myth", "worth it", "should you", "best")


def _keyword_bonus(title: str, keywords: tuple[str, ...], bonus: float, cap: float) -> float:
    lowered = title.lower()
    hits = sum(1 for keyword in keywords if keyword in lowered)
    return min(hits * bonus, cap)


def _affiliate_potential(candidate: ResearchCandidate) -> float:
    """Do not force an affiliate opportunity onto content with no commercial intent."""
    if not candidate.monetization_path:
        return 0.5
    path = candidate.monetization_path.lower()
    if "growth only" in path:
        return 1.0
    if "affiliate" in path:
        return 8.0
    if "upgrade" in path or "follow-up" in path:
        return 6.0
    return 4.0


def _freshness(candidate: ResearchCandidate) -> float:
    published_at_raw = candidate.raw_metadata.get("published_at") if candidate.raw_metadata else None
    if not published_at_raw:
        # No verified publish date available -- neutral default, not a guess at recency.
        return 5.0
    try:
        published_at = datetime.fromisoformat(published_at_raw)
    except (TypeError, ValueError):
        return 5.0
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - published_at).total_seconds() / 86400
    if age_days <= 2:
        return 9.0
    if age_days <= 7:
        return 7.0
    if age_days <= 30:
        return 5.0
    return 3.0


def _confidence(candidate: ResearchCandidate) -> float:
    """Editorial confidence, not statistical confidence -- there is no real data yet.

    Curated fixture candidates carry more editorial confidence than an
    unverified, unfact-checked RSS headline.
    """
    if candidate.source_name.startswith("fixture"):
        return 6.0
    return 3.5


def score_candidate(candidate: ResearchCandidate) -> ScoreBreakdown:
    known_scores = (candidate.raw_metadata or {}).get("known_scores", {})
    if not isinstance(known_scores, dict):
        logger.warning(
            "Ignoring non-dict known_scores for candidate %s: %r",
            candidate.candidate_id,
            known_scores,
        )
        known_scores = {}
    heuristic_dimensions = set(SCORE_DIMENSIONS)

    values: dict[str, float] = {
        "audience_interest": _PILLAR_AUDIENCE_INTEREST[candidate.content_pillar]
        + _keyword_bonus(candidate.title, _NOVELTY_KEYWORDS, bonus=0.5, cap=1.5),
        "commercial_intent": _ROLE_COMMERCIAL_INTENT[candidate.content_role],
        "affiliate_potential": _affiliate_potential(candidate),
        # No authoritative competition data source exists yet (see CLAUDE.md
        # "Research and opportunity scoring rules") -- neutral midpoint
        # rather than a fabricated number.
        "competition": 5.0,
        "novelty": 4.0 + _keyword_bonus(candidate.title, _NOVELTY_KEYWORDS, bonus=1.0, cap=3.0),
        "visual_potential": _PILLAR_VISUAL_POTENTIAL[candidate.content_pillar],
        "retention_potential": 4.5 + _keyword_bonus(candidate.title, _RETENTION_KEYWORDS, bonus=1.0, cap=3.0),
        "production_difficulty": _PILLAR_PRODUCTION_DIFFICULTY[candidate.content_pillar],
        "confidence": _confidence(candidate),
        "freshness": _freshness(candidate),
        "evergreen_value": _PILLAR_EVERGREEN_VALUE[candidate.content_pillar],
    }

    for dimension, real_value in known_scores.items():
        if dimension not in values:
            continue
        try:
            values[dimension] = float(real_value)
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring non-numeric known_scores[%r]=%r for candidate %s",
                dimension,
                real_value,
                candidate.candidate_id,
            )
            continue
        heuristic_dimensions.discard(dimension)

    values = {dimension: max(0.0, min(10.0, value)) for dimension, value in values.items()}

    return ScoreBreakdown(**values, heuristic_dimensions=frozenset(heuristic_dimensions))
