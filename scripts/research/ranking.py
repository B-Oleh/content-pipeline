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

V0.2 adds a repetition penalty, applied AFTER the weighted sum above rather
than as a 12th score dimension -- it is not evidence about the topic itself,
it is a ranking-time adjustment reflecting how recently the same game was
last recommended (see docs/RESEARCH_AGENT.md "Game history repetition").
This keeps the documented 11-dimension ScoreBreakdown scale (see CLAUDE.md
"Research and opportunity scoring rules") unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from scripts.research import freshness as freshness_module
from scripts.research.game_history import days_since, get_previous_recommendations
from scripts.research.models import ResearchCandidate, ScoreBreakdown, ScoredCandidate

RANKING_FORMULA_VERSION = "v0.2"

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


# --- Game history repetition penalty (V0.2) --------------------------------
#
# A repeat is not automatically banned (see CLAUDE.md "Game recommendation
# integrity"): recommending the same game again soon, for the SAME hardware
# tier, is penalized heavily; the same game much later is barely penalized;
# and the same game for a DIFFERENT hardware tier -- a legitimately distinct
# angle -- only gets the (much smaller) cross-tier penalty below. Configurable
# via the two tables below; ranking.py is the only place this is applied.

# (max_days_since_last_recommendation, penalty) -- checked in order.
REPETITION_PENALTY_RULES: list[tuple[float, float]] = [
    (14.0, 4.0),
    (30.0, 3.0),
    (90.0, 1.5),
    (180.0, 0.5),
]
CROSS_TIER_PENALTY_MULTIPLIER = 0.25


def _penalty_for_days(days_since_value: Optional[float]) -> float:
    if days_since_value is None or days_since_value < 0:
        return 0.0
    for max_days, penalty in REPETITION_PENALTY_RULES:
        if days_since_value <= max_days:
            return penalty
    return 0.0


def compute_repetition_penalty(
    candidate: ResearchCandidate,
    game_history: list[dict[str, Any]],
    as_of: datetime,
) -> tuple[float, Optional[str]]:
    """Return (penalty, human-readable note) for a monthly-games candidate.

    note is grounded only in the candidate's own history entries -- never a
    fabricated rationale. Non-game candidates (no game_title) and games with
    no prior history always get (0.0, None).
    """
    if not candidate.game_title or not game_history:
        return 0.0, None

    matches = get_previous_recommendations(candidate.game_title, game_history)
    if not matches:
        return 0.0, None

    tier_value = candidate.hardware_tier.value if candidate.hardware_tier else None
    same_tier_matches = [match for match in matches if match.get("hardware_tier") == tier_value]

    if same_tier_matches:
        days = days_since(same_tier_matches, as_of)
        penalty = _penalty_for_days(days)
        scope = "for this hardware tier"
    else:
        days = days_since(matches, as_of)
        penalty = round(_penalty_for_days(days) * CROSS_TIER_PENALTY_MULTIPLIER, 2)
        scope = "for a different hardware tier"

    if penalty <= 0 or days is None:
        return 0.0, None

    note = f"{candidate.game_title!r} was previously recommended {scope} {days:.0f} day(s) ago"
    return round(penalty, 2), note


def rank_candidates(
    scored: list[tuple[ResearchCandidate, ScoreBreakdown]],
    game_history: Optional[list[dict[str, Any]]] = None,
    as_of: Optional[datetime] = None,
) -> list[ScoredCandidate]:
    """Score, sort (descending), and assign 1-based ranks to a batch of candidates.

    game_history (see game_history.py) drives the repetition penalty above;
    omitting it (the V0.1 call shape) is equivalent to an empty history --
    no candidate is penalized.
    """
    game_history = game_history or []
    as_of = as_of or datetime.now(timezone.utc)

    ranked = []
    for candidate, scores in scored:
        base_score = compute_overall_score(scores)
        penalty, note = compute_repetition_penalty(candidate, game_history, as_of)
        overall_score = round(max(0.0, base_score - penalty), 2)

        reasoning = _top_reasons(scores)
        if note:
            reasoning.append(f"repetition penalty -{penalty:.1f}: {note}")

        freshness_tier, _age_days = freshness_module.classify_published_at(
            (candidate.raw_metadata or {}).get("published_at"), as_of=as_of
        )

        ranked.append(
            ScoredCandidate(
                candidate=candidate,
                scores=scores,
                overall_score=overall_score,
                reasoning=reasoning,
                repetition_penalty=penalty,
                freshness_tier=freshness_tier.value,
            )
        )

    ranked.sort(key=lambda scored_candidate: scored_candidate.overall_score, reverse=True)
    for position, scored_candidate in enumerate(ranked, start=1):
        scored_candidate.rank = position
    return ranked
