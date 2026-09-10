from scripts.research.models import ContentPillar, ContentRole, ResearchCandidate, ScoreBreakdown
from scripts.research.ranking import RANKING_WEIGHTS, compute_overall_score, rank_candidates


def _score_kwargs(**overrides):
    kwargs = {
        "audience_interest": 5.0,
        "commercial_intent": 5.0,
        "affiliate_potential": 5.0,
        "competition": 5.0,
        "novelty": 5.0,
        "visual_potential": 5.0,
        "retention_potential": 5.0,
        "production_difficulty": 5.0,
        "confidence": 5.0,
        "freshness": 5.0,
        "evergreen_value": 5.0,
    }
    kwargs.update(overrides)
    return kwargs


def _candidate(candidate_id: str) -> ResearchCandidate:
    return ResearchCandidate(
        candidate_id=candidate_id,
        title=f"Topic {candidate_id}",
        content_pillar=ContentPillar.BUYING_ADVICE,
        content_role=ContentRole.HYBRID,
        source_name="fixture_test",
    )


def test_weights_sum_to_one():
    assert abs(sum(RANKING_WEIGHTS.values()) - 1.0) < 1e-6


def test_higher_audience_interest_yields_higher_overall_score():
    low = ScoreBreakdown(**_score_kwargs(audience_interest=2.0))
    high = ScoreBreakdown(**_score_kwargs(audience_interest=9.0))
    assert compute_overall_score(high) > compute_overall_score(low)


def test_higher_production_difficulty_lowers_overall_score():
    easy = ScoreBreakdown(**_score_kwargs(production_difficulty=1.0))
    hard = ScoreBreakdown(**_score_kwargs(production_difficulty=9.0))
    assert compute_overall_score(easy) > compute_overall_score(hard)


def test_higher_competition_lowers_overall_score():
    low_competition = ScoreBreakdown(**_score_kwargs(competition=1.0))
    high_competition = ScoreBreakdown(**_score_kwargs(competition=9.0))
    assert compute_overall_score(low_competition) > compute_overall_score(high_competition)


def test_rank_candidates_orders_descending_and_assigns_ranks():
    weak = (_candidate("weak"), ScoreBreakdown(**_score_kwargs(audience_interest=1.0)))
    strong = (_candidate("strong"), ScoreBreakdown(**_score_kwargs(audience_interest=9.0)))

    ranked = rank_candidates([weak, strong])

    assert [sc.candidate.candidate_id for sc in ranked] == ["strong", "weak"]
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2
    assert ranked[0].overall_score >= ranked[1].overall_score


def test_rank_candidates_produces_reasoning_grounded_in_scores():
    scores = ScoreBreakdown(**_score_kwargs(audience_interest=10.0))
    ranked = rank_candidates([(_candidate("c1"), scores)])
    assert ranked[0].reasoning
    assert any("audience_interest" in reason for reason in ranked[0].reasoning)
