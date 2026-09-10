from datetime import datetime, timedelta, timezone

from scripts.research.models import ContentPillar, ContentRole, HardwareTier, ResearchCandidate
from scripts.research.ranking import compute_repetition_penalty, rank_candidates
from scripts.research.models import ScoreBreakdown

AS_OF = datetime(2026, 6, 15, tzinfo=timezone.utc)


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


def _game_candidate(hardware_tier: HardwareTier, game_title: str = "Hades II") -> ResearchCandidate:
    return ResearchCandidate(
        candidate_id=f"c-{hardware_tier.value}",
        title=f"What to play on {hardware_tier.value} this month",
        content_pillar=ContentPillar.MONTHLY_GAMES,
        content_role=ContentRole.GROWTH,
        source_name="fixture_test",
        hardware_tier=hardware_tier,
        game_title=game_title,
    )


def _history_entry(game_title: str, hardware_tier: str, days_ago: float) -> dict:
    return {
        "game_title": game_title,
        "hardware_tier": hardware_tier,
        "recommended_at": (AS_OF - timedelta(days=days_ago)).isoformat(),
    }


def test_no_history_means_no_penalty():
    candidate = _game_candidate(HardwareTier.LOW_END)
    penalty, note = compute_repetition_penalty(candidate, [], AS_OF)
    assert penalty == 0.0
    assert note is None


def test_non_game_candidate_never_penalized():
    candidate = ResearchCandidate(
        candidate_id="c1",
        title="Best budget GPUs",
        content_pillar=ContentPillar.BUYING_ADVICE,
        content_role=ContentRole.REVENUE,
        source_name="fixture_test",
    )
    history = [_history_entry("Hades II", "low_end", 1)]
    penalty, note = compute_repetition_penalty(candidate, history, AS_OF)
    assert penalty == 0.0
    assert note is None


def test_recent_same_tier_recommendation_gets_strong_penalty():
    candidate = _game_candidate(HardwareTier.LOW_END)
    history = [_history_entry("Hades II", "low_end", 10)]
    penalty, note = compute_repetition_penalty(candidate, history, AS_OF)
    assert penalty >= 3.0
    assert "Hades II" in note


def test_old_same_tier_recommendation_gets_little_or_no_penalty():
    candidate = _game_candidate(HardwareTier.LOW_END)
    history = [_history_entry("Hades II", "low_end", 240)]  # ~8 months
    penalty, note = compute_repetition_penalty(candidate, history, AS_OF)
    assert penalty == 0.0


def test_different_tier_recommendation_gets_reduced_penalty():
    candidate = _game_candidate(HardwareTier.HIGH_END)
    same_tier_recent = _history_entry("Hades II", "low_end", 10)

    penalty_cross_tier, _ = compute_repetition_penalty(candidate, [same_tier_recent], AS_OF)

    same_tier_candidate = _game_candidate(HardwareTier.LOW_END)
    penalty_same_tier, _ = compute_repetition_penalty(same_tier_candidate, [same_tier_recent], AS_OF)

    assert 0.0 <= penalty_cross_tier < penalty_same_tier


def test_rank_candidates_applies_penalty_to_overall_score_and_orders_accordingly():
    scores = ScoreBreakdown(**_score_kwargs())
    fresh_pick = _game_candidate(HardwareTier.LOW_END, game_title="New Game")
    repeated_pick = _game_candidate(HardwareTier.LOW_END, game_title="Hades II")
    # Give repeated_pick a distinct candidate_id to avoid dedup collisions in this direct ranking test.
    repeated_pick.candidate_id = "c-repeated"

    history = [_history_entry("Hades II", "low_end", 5)]

    ranked = rank_candidates(
        [(fresh_pick, scores), (repeated_pick, scores)],
        game_history=history,
        as_of=AS_OF,
    )

    ranked_by_id = {sc.candidate.candidate_id: sc for sc in ranked}
    assert ranked_by_id[repeated_pick.candidate_id].repetition_penalty > 0.0
    assert ranked_by_id[fresh_pick.candidate_id].repetition_penalty == 0.0
    assert ranked_by_id[fresh_pick.candidate_id].overall_score > ranked_by_id[repeated_pick.candidate_id].overall_score
    assert ranked[0].candidate.candidate_id == fresh_pick.candidate_id


def test_rank_candidates_without_game_history_behaves_like_v01():
    scores = ScoreBreakdown(**_score_kwargs())
    candidate = _game_candidate(HardwareTier.LOW_END)
    ranked = rank_candidates([(candidate, scores)])
    assert ranked[0].repetition_penalty == 0.0
