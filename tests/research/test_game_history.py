from datetime import datetime, timezone

from scripts.research.game_history import load_game_history, record_game_recommendations
from scripts.research.models import (
    ContentPillar,
    ContentRole,
    HardwareTier,
    GamePriceType,
    ResearchCandidate,
    ResearchResult,
    ScoreBreakdown,
    ScoredCandidate,
)


def _score_kwargs():
    return {
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


def _game_result() -> ResearchResult:
    game_candidate = ResearchCandidate(
        candidate_id="g1",
        title="What to play this month on a low-end PC",
        content_pillar=ContentPillar.MONTHLY_GAMES,
        content_role=ContentRole.GROWTH,
        source_name="fixture_test",
        hardware_tier=HardwareTier.LOW_END,
        game_price_type=GamePriceType.FREE,
        game_title="Team Fortress 2",
    )
    non_game_candidate = ResearchCandidate(
        candidate_id="n1",
        title="Best budget GPUs",
        content_pillar=ContentPillar.BUYING_ADVICE,
        content_role=ContentRole.REVENUE,
        source_name="fixture_test",
    )
    scores = ScoreBreakdown(**_score_kwargs())
    return ResearchResult(
        generated_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        ranking_formula_version="v0.1",
        candidates=[
            ScoredCandidate(candidate=game_candidate, scores=scores, overall_score=6.0, rank=1),
            ScoredCandidate(candidate=non_game_candidate, scores=scores, overall_score=5.0, rank=2),
        ],
    )


def test_record_game_recommendations_only_records_candidates_with_a_game_title(tmp_path):
    record_game_recommendations(_game_result(), base_dir=tmp_path)

    history = load_game_history(tmp_path)

    assert len(history) == 1
    assert history[0]["game_title"] == "Team Fortress 2"
    assert history[0]["hardware_tier"] == "low_end"
    assert history[0]["game_price_type"] == "free"


def test_record_game_recommendations_appends_across_runs(tmp_path):
    record_game_recommendations(_game_result(), base_dir=tmp_path)
    record_game_recommendations(_game_result(), base_dir=tmp_path)

    history = load_game_history(tmp_path)

    assert len(history) == 2


def test_load_game_history_returns_empty_list_when_no_file(tmp_path):
    assert load_game_history(tmp_path) == []
