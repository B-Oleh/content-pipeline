from datetime import datetime, timezone

import pytest

from scripts.production.topic_selection import (
    NoSuitableCandidateError,
    estimate_visual_producibility,
    select_topic_candidate,
)
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


def _scored(candidate_id, pillar, overall_score, rank, title=None, summary=None):
    candidate = ResearchCandidate(
        candidate_id=candidate_id,
        title=title or f"PC gaming topic {candidate_id}",
        content_pillar=pillar,
        content_role=ContentRole.GROWTH,
        source_name="fixture_test",
        summary=summary,
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

    chosen = select_topic_candidate(_result([low_ranked, preferred]), preferred_title="PC gaming topic b")

    assert chosen.candidate.candidate_id == "b"


def test_preferred_title_falls_back_to_normal_selection_when_not_found(caplog):
    only = _scored("a", ContentPillar.OPTIMIZATION, overall_score=5.0, rank=1)

    with caplog.at_level("WARNING"):
        chosen = select_topic_candidate(_result([only]), preferred_title="A topic that no longer exists")

    assert chosen.candidate.candidate_id == "a"
    assert any("no longer present" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Part 2: visual producibility -- a secondary, same-pillar-tier tie-breaker
# ---------------------------------------------------------------------------


def test_estimate_visual_producibility_rewards_concrete_hardware_vocabulary():
    concrete = _scored(
        "a", ContentPillar.OPTIMIZATION, overall_score=5.0, rank=1,
        title="Upgrade your GPU and RAM for a faster gaming PC build",
    )
    abstract = _scored(
        "b", ContentPillar.OPTIMIZATION, overall_score=5.0, rank=2,
        title="Is PC gaming still worth it in the long run",
    )

    assert estimate_visual_producibility(concrete.candidate) > estimate_visual_producibility(abstract.candidate)


def test_producibility_never_overrides_pillar_reliability_tier():
    """A monthly_games candidate stuffed with hardware vocabulary is still
    pillar-tier 0 -- it still needs a specific game's footage, which stock
    libraries will not have (see PILLAR_VISUAL_RELIABILITY's own
    docstring)."""
    hardware_heavy_low_tier = _scored(
        "a", ContentPillar.MONTHLY_GAMES, overall_score=9.0, rank=1,
        title="GPU CPU RAM SSD motherboard hardware build",
    )
    plain_high_tier = _scored("b", ContentPillar.OPTIMIZATION, overall_score=1.0, rank=2, title="PC gaming topic")

    chosen = select_topic_candidate(_result([hardware_heavy_low_tier, plain_high_tier]))

    assert chosen.candidate.candidate_id == "b"


def test_deprioritizes_visually_weak_topic_within_the_same_reliability_tier():
    """Two candidates in the same pillar tier with the same overall_score --
    the one with more concrete, illustrable vocabulary is preferred, per
    the task's "deprioritize (not exclude) candidate topics too likely to
    become a slideshow" requirement."""
    concrete = _scored(
        "a", ContentPillar.OPTIMIZATION, overall_score=5.0, rank=2,
        title="Best GPU and monitor upgrades for your gaming desktop setup",
    )
    abstract = _scored(
        "b", ContentPillar.OPTIMIZATION, overall_score=5.0, rank=1,
        title="Why gaming culture keeps changing over time",
    )

    chosen = select_topic_candidate(_result([concrete, abstract]))

    assert chosen.candidate.candidate_id == "a"


@pytest.mark.parametrize("title", [
    "Best grocery discounts this month", "Movie reviews and celebrity news",
    "Football championship results", "A greeting card for your birthday",
    "A new business case for memory training",
    "Steam cleaning tips for your kitchen",
    "A ram joins the sheep flock",
    "Ram truck buying advice",
])
def test_off_topic_is_rejected_even_with_valid_pillar_or_override(title):
    bad = _scored("bad", ContentPillar.OPTIMIZATION, 10, 1, title=title)
    with pytest.raises(NoSuitableCandidateError):
        select_topic_candidate(_result([bad]), preferred_title=title)
    good = _scored("good", ContentPillar.OPTIMIZATION, 1, 2, title="PC cooling mistakes")
    assert select_topic_candidate(_result([bad, good]), preferred_title=title) == good


@pytest.mark.parametrize("title", [
    "Steam Deck cooling tips", "Best games on Steam", "Steam library tips",
    "Is 16 GB of RAM enough?", "RAM timings explained", "DDR5 buying advice",
])
def test_ambiguous_terms_with_computing_context_are_accepted(title):
    good = _scored("good", ContentPillar.OPTIMIZATION, 5, 1, title=title)
    assert select_topic_candidate(_result([good])) == good
