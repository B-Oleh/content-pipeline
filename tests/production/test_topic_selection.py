from datetime import datetime, timezone

import pytest

from scripts.production.topic_selection import NoSuitableCandidateError, select_topic_candidate
from scripts.research.models import (
    ContentPillar,
    ContentRole,
    ResearchCandidate,
    ResearchResult,
    ScoreBreakdown,
    ScoredCandidate,
)


def _score_kwargs():
    return {
        "audience_interest": 5.0, "commercial_intent": 5.0, "affiliate_potential": 5.0,
        "competition": 5.0, "novelty": 5.0, "visual_potential": 5.0, "retention_potential": 5.0,
        "production_difficulty": 5.0, "confidence": 5.0, "freshness": 5.0, "evergreen_value": 5.0,
    }


def _scored(candidate_id, pillar, overall_score, rank):
    candidate = ResearchCandidate(
        candidate_id=candidate_id,
        title=f"Topic {candidate_id}",
        content_pillar=pillar,
        content_role=ContentRole.GROWTH,
        source_name="fixture_test",
    )
    return ScoredCandidate(candidate=candidate, scores=ScoreBreakdown(**_score_kwargs()), overall_score=overall_score, rank=rank)


def _result(candidates):
    return ResearchResult(generated_at=datetime.now(timezone.utc), ranking_formula_version="v0.2", candidates=candidates)


def test_raises_when_no_candidates():
    with pytest.raises(NoSuitableCandidateError):
        select_topic_candidate(_result([]))


def test_prefers_visually_reliable_pillar_even_if_lower_ranked():
    monthly_games_top = _scored("a", ContentPillar.MONTHLY_GAMES, overall_score=9.0, rank=1)
    optimization_second = _scored("b", ContentPillar.OPTIMIZATION, overall_score=6.0, rank=2)

    chosen = select_topic_candidate(_result([monthly_games_top, optimization_second]))

    assert chosen.candidate.candidate_id == "b"


def test_breaks_ties_within_same_reliability_by_score():
    first = _scored("a", ContentPillar.GAMING_TECHNOLOGY, overall_score=5.0, rank=2)
    second = _scored("b", ContentPillar.OPTIMIZATION, overall_score=7.0, rank=1)

    chosen = select_topic_candidate(_result([first, second]))

    assert chosen.candidate.candidate_id == "b"


def test_falls_back_to_best_overall_when_nothing_reliable(caplog):
    only = _scored("a", ContentPillar.GAME_RECOMMENDATIONS, overall_score=4.0, rank=1)

    with caplog.at_level("WARNING"):
        chosen = select_topic_candidate(_result([only]))

    assert chosen.candidate.candidate_id == "a"
    assert any("low visual reliability" in record.message for record in caplog.records)


def test_preferred_title_is_selected_when_present():
    """Used by the Telegram "Regenerate" button (see telegram_approval.py)
    to re-select the same topic in a fresh Research Agent run."""
    low_ranked = _scored("a", ContentPillar.OPTIMIZATION, overall_score=3.0, rank=2)
    preferred = _scored("b", ContentPillar.GAME_RECOMMENDATIONS, overall_score=2.0, rank=3)

    chosen = select_topic_candidate(_result([low_ranked, preferred]), preferred_title="Topic b")

    assert chosen.candidate.candidate_id == "b"


def test_preferred_title_falls_back_to_normal_selection_when_not_found(caplog):
    only = _scored("a", ContentPillar.OPTIMIZATION, overall_score=5.0, rank=1)

    with caplog.at_level("WARNING"):
        chosen = select_topic_candidate(_result([only]), preferred_title="A topic that no longer exists")

    assert chosen.candidate.candidate_id == "a"
    assert any("no longer present" in record.message for record in caplog.records)
