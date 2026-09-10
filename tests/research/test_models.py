import pytest

from scripts.research.models import (
    ContentPillar,
    ContentRole,
    ResearchCandidate,
    ResearchResult,
    ScoreBreakdown,
    ScoredCandidate,
)


def _valid_score_kwargs(**overrides):
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


def test_score_breakdown_accepts_valid_range():
    scores = ScoreBreakdown(**_valid_score_kwargs(audience_interest=0.0, novelty=10.0))
    assert scores.audience_interest == 0.0
    assert scores.novelty == 10.0


@pytest.mark.parametrize("bad_value", [-0.1, 10.1, 100])
def test_score_breakdown_rejects_out_of_range(bad_value):
    with pytest.raises(ValueError):
        ScoreBreakdown(**_valid_score_kwargs(audience_interest=bad_value))


def test_score_breakdown_rejects_unknown_heuristic_dimension():
    with pytest.raises(ValueError):
        ScoreBreakdown(**_valid_score_kwargs(), heuristic_dimensions=frozenset({"not_a_dimension"}))


def test_score_breakdown_round_trips_through_dict():
    scores = ScoreBreakdown(**_valid_score_kwargs())
    restored = ScoreBreakdown.from_dict(scores.to_dict())
    assert restored == scores


def _valid_candidate(**overrides) -> ResearchCandidate:
    kwargs = {
        "candidate_id": "c1",
        "title": "Best budget GPUs",
        "content_pillar": ContentPillar.BUYING_ADVICE,
        "content_role": ContentRole.HYBRID,
        "source_name": "fixture_test",
    }
    kwargs.update(overrides)
    return ResearchCandidate(**kwargs)


def test_candidate_requires_non_empty_title():
    with pytest.raises(ValueError):
        _valid_candidate(title="   ")


def test_candidate_requires_non_empty_id():
    with pytest.raises(ValueError):
        _valid_candidate(candidate_id="")


def test_candidate_rejects_invalid_pillar_via_from_dict():
    data = _valid_candidate().to_dict()
    data["content_pillar"] = "not_a_real_pillar"
    with pytest.raises(ValueError):
        ResearchCandidate.from_dict(data)


def test_candidate_round_trips_through_dict():
    candidate = _valid_candidate(hardware_tier=None, monetization_path="gaming mouse affiliate links")
    restored = ResearchCandidate.from_dict(candidate.to_dict())
    assert restored.title == candidate.title
    assert restored.content_pillar == candidate.content_pillar
    assert restored.monetization_path == candidate.monetization_path


def test_research_result_round_trips_through_dict():
    candidate = _valid_candidate()
    scores = ScoreBreakdown(**_valid_score_kwargs())
    scored = ScoredCandidate(candidate=candidate, scores=scores, overall_score=6.5, rank=1, reasoning=["x scored high"])
    from datetime import datetime, timezone

    result = ResearchResult(
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ranking_formula_version="v0.1",
        candidates=[scored],
        source_errors=["some_source: timed out"],
    )
    restored = ResearchResult.from_dict(result.to_dict())
    assert restored.ranking_formula_version == "v0.1"
    assert restored.source_errors == ["some_source: timed out"]
    assert len(restored.candidates) == 1
    assert restored.candidates[0].candidate.title == candidate.title
    assert restored.candidates[0].overall_score == 6.5
