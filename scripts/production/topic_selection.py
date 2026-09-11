"""Pick one topic from a Research Agent run for the video pipeline to produce.

Reuses Research Agent's own ranking (scripts/research/ranking.py) as the
base signal -- this module only re-prioritizes among already-ranked
candidates for visual feasibility, it never re-scores or second-guesses the
research itself.
"""

from __future__ import annotations

from typing import Optional

from scripts.research.models import ContentPillar, ResearchResult, ScoredCandidate
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Higher = more reliably illustrated with generic stock/contextual footage.
# game_recommendations/monthly_games need a *specific* game's footage, which
# stock libraries will not have -- see docs/PRODUCTION_PIPELINE.md "Topic
# selection" for the reasoning (avoid pretending generic footage shows a
# specific named product/game).
PILLAR_VISUAL_RELIABILITY: dict[ContentPillar, int] = {
    ContentPillar.OPTIMIZATION: 3,
    ContentPillar.MISTAKES_AND_MYTHS: 3,
    ContentPillar.GAMING_TECHNOLOGY: 3,
    ContentPillar.BUYING_ADVICE: 2,
    ContentPillar.HARDWARE_COMPARISON: 1,
    ContentPillar.GAME_RECOMMENDATIONS: 0,
    ContentPillar.MONTHLY_GAMES: 0,
}


class NoSuitableCandidateError(RuntimeError):
    """Raised when a Research Agent run produced no candidates at all."""


def select_topic_candidate(result: ResearchResult, preferred_title: Optional[str] = None) -> ScoredCandidate:
    """Pick the candidate to produce a video for.

    If `preferred_title` is given (set by the "Regenerate" Telegram button
    -- see telegram_approval.py::trigger_regeneration_workflow and
    produce_video.py) and a candidate with that exact title is present in
    this run's fresh Research Agent results, it is selected directly --
    this is what "regenerate the same topic/content intent" means in
    practice, since Research Agent re-runs from scratch each time and does
    not guarantee an identical candidate set. If the title is not found
    (the topic may no longer be current), falls back to normal selection
    below with a logged warning rather than failing the run.

    Otherwise, prefers the pillar most reliably illustrated with generic
    stock footage among the top-ranked candidates; falls back to the single
    best-ranked candidate overall (with a warning) if none of the
    reliably-illustrated pillars are present, rather than failing the run
    outright.
    """
    if not result.candidates:
        raise NoSuitableCandidateError("Research Agent produced no candidates to select a topic from")

    if preferred_title:
        for scored in result.candidates:
            if scored.candidate.title == preferred_title:
                logger.info("Regeneration requested %r -- found and re-selected the same topic", preferred_title)
                return scored
        logger.warning(
            "Regeneration requested topic %r, but it is no longer present in this run's Research Agent "
            "results -- falling back to normal topic selection",
            preferred_title,
        )

    def sort_key(scored: ScoredCandidate) -> tuple[int, float]:
        reliability = PILLAR_VISUAL_RELIABILITY.get(scored.candidate.content_pillar, 0)
        return (reliability, scored.overall_score)

    best = max(result.candidates, key=sort_key)

    if PILLAR_VISUAL_RELIABILITY.get(best.candidate.content_pillar, 0) == 0:
        logger.warning(
            "Selected topic %r (pillar %s) has low visual reliability -- generic contextual "
            "footage will be used, and the script must not claim it shows a specific game/product",
            best.candidate.title,
            best.candidate.content_pillar.value,
        )
    logger.info(
        "Selected topic: %r (pillar=%s, rank=%s, overall_score=%.2f)",
        best.candidate.title,
        best.candidate.content_pillar.value,
        best.rank,
        best.overall_score,
    )
    return best
