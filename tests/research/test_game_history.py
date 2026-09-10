from datetime import datetime, timedelta, timezone

from scripts.research.game_history import (
    days_since,
    get_previous_recommendations,
    load_game_history,
    record_game_recommendations,
)
from scripts.research.state.store import JsonListStore
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


def test_record_game_recommendations_uses_a_nested_cross_platform_path(tmp_path):
    # base_dir composed via pathlib, exercising directory creation regardless
    # of the platform's path separator.
    state_dir = tmp_path / "research" / "state"
    record_game_recommendations(_game_result(), base_dir=state_dir)

    assert (state_dir / "game_history.json").exists()
    assert load_game_history(state_dir) == JsonListStore(state_dir / "game_history.json").load()


def test_get_previous_recommendations_matches_case_insensitively():
    history = [
        {"game_title": "Hades II", "hardware_tier": "low_end", "recommended_at": "2026-01-01T00:00:00+00:00"},
        {"game_title": "cyberpunk 2077", "hardware_tier": "high_end", "recommended_at": "2026-02-01T00:00:00+00:00"},
    ]
    matches = get_previous_recommendations("hades ii", history)
    assert len(matches) == 1
    assert matches[0]["hardware_tier"] == "low_end"


def test_get_previous_recommendations_no_match_returns_empty():
    history = [{"game_title": "Hades II", "recommended_at": "2026-01-01T00:00:00+00:00"}]
    assert get_previous_recommendations("Some Other Game", history) == []


def test_days_since_uses_most_recent_entry():
    as_of = datetime(2026, 3, 1, tzinfo=timezone.utc)
    entries = [
        {"recommended_at": (as_of - timedelta(days=30)).isoformat()},
        {"recommended_at": (as_of - timedelta(days=5)).isoformat()},
    ]
    result = days_since(entries, as_of)
    assert 4.9 < result < 5.1


def test_days_since_returns_none_for_no_valid_entries():
    as_of = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert days_since([], as_of) is None


def test_days_since_skips_malformed_entries_instead_of_raising():
    as_of = datetime(2026, 3, 1, tzinfo=timezone.utc)
    entries = [
        {"recommended_at": "not-a-date"},
        {"recommended_at": None},
        {},
        {"recommended_at": (as_of - timedelta(days=10)).isoformat()},
    ]
    result = days_since(entries, as_of)
    assert 9.9 < result < 10.1


def test_days_since_handles_naive_recommended_at():
    as_of = datetime(2026, 3, 1, tzinfo=timezone.utc)
    naive_entry = [{"recommended_at": (as_of - timedelta(days=2)).replace(tzinfo=None).isoformat()}]
    result = days_since(naive_entry, as_of)
    assert 1.9 < result < 2.1
