from scripts.research.models import SCORE_DIMENSIONS, ContentPillar, ContentRole, ResearchCandidate
from scripts.research.scoring import score_candidate


def _candidate(**overrides) -> ResearchCandidate:
    kwargs = {
        "candidate_id": "c1",
        "title": "Best budget GPUs under $200",
        "content_pillar": ContentPillar.BUYING_ADVICE,
        "content_role": ContentRole.HYBRID,
        "source_name": "fixture_test",
        "monetization_path": "GPU affiliate links",
    }
    kwargs.update(overrides)
    return ResearchCandidate(**kwargs)


def test_score_candidate_returns_values_in_range():
    scores = score_candidate(_candidate())
    for dimension in SCORE_DIMENSIONS:
        value = getattr(scores, dimension)
        assert 0.0 <= value <= 10.0


def test_score_candidate_marks_everything_heuristic_by_default():
    scores = score_candidate(_candidate())
    assert scores.heuristic_dimensions == frozenset(SCORE_DIMENSIONS)


def test_known_scores_override_and_clear_heuristic_flag():
    candidate = _candidate(raw_metadata={"known_scores": {"audience_interest": 9.5}})
    scores = score_candidate(candidate)
    assert scores.audience_interest == 9.5
    assert "audience_interest" not in scores.heuristic_dimensions
    assert "novelty" in scores.heuristic_dimensions


def test_no_monetization_path_yields_low_affiliate_potential():
    candidate = _candidate(monetization_path=None)
    scores = score_candidate(candidate)
    assert scores.affiliate_potential <= 1.0


def test_growth_only_monetization_path_yields_low_affiliate_potential():
    candidate = _candidate(monetization_path="audience growth only")
    scores = score_candidate(candidate)
    assert scores.affiliate_potential <= 1.5


def test_affiliate_monetization_path_yields_high_affiliate_potential():
    candidate = _candidate(monetization_path="gaming mouse affiliate links")
    scores = score_candidate(candidate)
    assert scores.affiliate_potential >= 7.0


def test_revenue_role_scores_higher_commercial_intent_than_growth_role():
    revenue_scores = score_candidate(_candidate(content_role=ContentRole.REVENUE))
    growth_scores = score_candidate(_candidate(content_role=ContentRole.GROWTH))
    assert revenue_scores.commercial_intent > growth_scores.commercial_intent


def test_competition_is_neutral_midpoint_not_fabricated():
    scores = score_candidate(_candidate())
    assert scores.competition == 5.0


def test_non_dict_known_scores_is_ignored_not_fatal():
    candidate = _candidate(raw_metadata={"known_scores": "not_a_dict"})
    scores = score_candidate(candidate)
    assert scores.heuristic_dimensions == frozenset(SCORE_DIMENSIONS)


def test_non_numeric_known_score_value_is_ignored_not_fatal():
    candidate = _candidate(raw_metadata={"known_scores": {"audience_interest": "very high"}})
    scores = score_candidate(candidate)
    assert "audience_interest" in scores.heuristic_dimensions
    assert 0.0 <= scores.audience_interest <= 10.0
