"""Ranking engine for Research Agent V0.1.

RANKING_FORMULA_VERSION is persisted with every result so that future
changes to the weights below can be identified in historical data (see
CLAUDE.md "Analytics and learning feedback loop").

Business objective (see docs/BUSINESS_STRATEGY.md): reach the first $100 in
affiliate revenue while still growing an audience -- so growth signals
(audience_interest, retention_potential) and monetization signals
(commercial_intent, affiliate_potential) are weighted close to evenly,
rather than maximizing commercial intent alone (see CLAUDE.md "Ranking").

competition is given a deliberately small weight: we do not yet have
authoritative competition data (see CLAUDE.md "Research and opportunity
scoring rules"), so it should nudge ranking, not dominate it.

production_difficulty and competition are "inverted" dimensions: a higher
raw score is worse, so their contribution uses (10 - value) before applying
the weight.

Weights are a plain dict specifically so they are easy to tune later without
touching the ranking algorithm itself.
"""

from __future__ import annotations

from scripts.research.models import ResearchCandidate, ScoreBreakdown, ScoredCandidate

RANKING_FORMULA_VERSION = "v0.1"

RANKING_WEIGHTS: dict[str, float] = {
    "audience_interest": 0.16,
    "retention_potential": 0.14,
    "commercial_intent": 0.12,
    "affiliate_potential": 0.14,
    "confidence": 0.10,
    "freshness": 0.08,
    "evergreen_value": 0.08,
    "novelty": 0.06,
    "visual_potential": 0.04,
    "production_difficulty": 0.05,  # inverted
    "competition": 0.03,  # inverted, intentionally small -- see module docstring
}

INVERTED_DIMENSIONS = frozenset({"production_difficulty", "competition"})

_weight_sum = sum(RANKING_WEIGHTS.values())
if abs(_weight_sum - 1.0) > 1e-6:
    raise AssertionError(f"RANKING_WEIGHTS must sum to 1.0, got {_weight_sum}")


def _effective_value(scores: ScoreBreakdown, dimension: str) -> float:
    value = getattr(scores, dimension)
    return (10.0 - value) if dimension in INVERTED_DIMENSIONS else value


def compute_overall_score(scores: ScoreBreakdown) -> float:
    total = sum(weight * _effective_value(scores, dimension) for dimension, weight in RANKING_WEIGHTS.items())
    return round(total, 2)


def _top_reasons(scores: ScoreBreakdown, limit: int = 3) -> list[str]:
    """Explain a rank using only the candidate's own scores -- never a fabricated rationale."""
    contributions = [
        (dimension, weight * _effective_value(scores, dimension)) for dimension, weight in RANKING_WEIGHTS.items()
    ]
    contributions.sort(key=lambda pair: pair[1], reverse=True)
    return [f"{dimension} scored {getattr(scores, dimension):.1f}/10" for dimension, _ in contributions[:limit]]


def rank_candidates(scored: list[tuple[ResearchCandidate, ScoreBreakdown]]) -> list[ScoredCandidate]:
    """Score, sort (descending), and assign 1-based ranks to a batch of candidates."""
    ranked = [
        ScoredCandidate(
            candidate=candidate,
            scores=scores,
            overall_score=compute_overall_score(scores),
            reasoning=_top_reasons(scores),
        )
        for candidate, scores in scored
    ]
    ranked.sort(key=lambda scored_candidate: scored_candidate.overall_score, reverse=True)
    for position, scored_candidate in enumerate(ranked, start=1):
        scored_candidate.rank = position
    return ranked
