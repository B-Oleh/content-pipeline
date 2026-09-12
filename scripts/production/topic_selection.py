"""Pick one topic from a Research Agent run for the video pipeline to produce.

Reuses Research Agent's own ranking (scripts/research/ranking.py) as the
base signal -- this module only re-prioritizes among already-ranked
candidates for visual feasibility, it never re-scores or second-guesses the
research itself.
"""

from __future__ import annotations

import re
from typing import Optional

from scripts.research.models import ContentPillar, ResearchCandidate, ResearchResult, ScoredCandidate
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Higher = more reliably illustrated with generic stock/contextual footage.
# game_recommendations/monthly_games need a *specific* game's footage, which
# stock libraries will not have -- see docs/PRODUCTION_PIPELINE.md "Topic
# selection" for the reasoning (avoid pretending generic footage shows a
# specific named product/game). This is the PRIMARY, dominant signal in
# sort_key() below; estimate_visual_producibility() is a secondary,
# same-tier tie-breaker only -- see its own docstring.
PILLAR_VISUAL_RELIABILITY: dict[ContentPillar, int] = {
    ContentPillar.OPTIMIZATION: 3,
    ContentPillar.MISTAKES_AND_MYTHS: 3,
    ContentPillar.GAMING_TECHNOLOGY: 3,
    ContentPillar.BUYING_ADVICE: 2,
    ContentPillar.HARDWARE_COMPARISON: 1,
    ContentPillar.GAME_RECOMMENDATIONS: 0,
    ContentPillar.MONTHLY_GAMES: 0,
}

# Concrete, easily-stock-photographed PC/gaming hardware vocabulary. A
# candidate whose title/summary mentions several of these is a real signal
# that scenes about it will find genuine, honest visual matches (a GPU
# close-up, a gaming desk, a monitor) -- see estimate_visual_producibility().
# This does not change the business niche (every candidate is already
# PC-gaming/hardware content); it only distinguishes concrete, illustrable
# topics from abstract ones within that niche.
_EASY_TO_ILLUSTRATE_KEYWORDS = {
    "gpu", "cpu", "graphics", "card", "processor", "monitor", "monitors", "laptop", "laptops",
    "desktop", "keyboard", "keyboards", "mouse", "headset", "headsets", "ram", "memory",
    "ssd", "storage", "cooler", "cooling", "case", "cases", "motherboard", "setup", "build",
    "hardware", "pc", "computer", "computers", "gaming", "rig", "peripheral", "peripherals",
    "webcam", "microphone", "upgrade", "upgrading",
}
_MAX_PRODUCIBILITY_KEYWORD_HITS = 5
_PRODUCIBILITY_BONUS_PER_HIT = 0.1


class NoSuitableCandidateError(RuntimeError):
    """Raised when a Research Agent run contains no eligible niche candidates."""


def estimate_visual_producibility(candidate: ResearchCandidate) -> float:
    """A small, deterministic secondary signal (0.0-0.5) estimating how
    likely a candidate's own scenes are to find honest, concrete stock
    visuals -- based purely on how much concrete PC/gaming-hardware
    vocabulary its title/summary already contains (see
    _EASY_TO_ILLUSTRATE_KEYWORDS).

    This is deliberately a TIE-BREAKER, not a primary ranking factor: it
    never overrides `PILLAR_VISUAL_RELIABILITY` (a monthly_games candidate
    mentioning "GPU" several times is still pillar-tier 0, since it still
    needs a *specific game's* footage) -- see sort_key() below. It exists
    because two candidates in the same pillar/tier can differ a lot in how
    concretely illustrable they are (e.g. "should you upgrade your GPU"
    vs. a vaguer "is PC gaming worth it" piece), and a real run producing
    too many info-card-only scenes is exactly the failure this factor is
    meant to reduce -- see asset_acquisition.py's own visual-density work.
    """
    text = f"{candidate.title} {candidate.summary or ''}".lower()
    words = set(re.findall(r"[a-z]+", text))
    hits = len(words & _EASY_TO_ILLUSTRATE_KEYWORDS)
    return min(hits, _MAX_PRODUCIBILITY_KEYWORD_HITS) * _PRODUCIBILITY_BONUS_PER_HIT


def is_on_topic(candidate: ResearchCandidate) -> bool:
    """Require explicit PC/gaming subject matter, never just an RSS pillar label."""
    text = f"{candidate.title} {candidate.summary or ''}".lower()
    return bool(re.search(
        r"\b(pc|gaming|gamer|gamers|gpu|cpu|ssd|motherboard|graphics card|"
        r"video games?|computer|computers|laptop|laptops|geforce|radeon|ryzen|"
        r"ddr[345]|random access memory|steam deck)\b", text
    ) or re.search(
        # Steam and ram also describe cleaning, animals, and vehicles.
        # Require an explicit computing context for these ambiguous terms.
        r"\bsteam\b.*\b(games?|store|library|wishlist)\b|"
        r"\b(games?|store|library|wishlist)\b.*\bsteam\b|"
        r"\b\d+\s*gb\s+(?:of\s+)?ram\b|"
        r"\bram\s+(?:capacity|speed|timings|latency|modules?|upgrade)\b", text
    ))


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
    candidates = [s for s in result.candidates if is_on_topic(s.candidate)]
    for scored in result.candidates:
        if scored not in candidates:
            logger.warning("Rejecting off-topic candidate: %r", scored.candidate.title)
    if not candidates:
        raise NoSuitableCandidateError("Research Agent produced no on-topic candidates to select a topic from")

    if preferred_title:
        for scored in candidates:
            if scored.candidate.title == preferred_title:
                logger.info("Regeneration requested %r -- found and re-selected the same topic", preferred_title)
                return scored
        logger.warning(
            "Regeneration requested topic %r, but it is no longer present in this run's Research Agent "
            "results -- falling back to normal topic selection",
            preferred_title,
        )

    def sort_key(scored: ScoredCandidate) -> tuple[int, float, float]:
        reliability = PILLAR_VISUAL_RELIABILITY.get(scored.candidate.content_pillar, 0)
        producibility = estimate_visual_producibility(scored.candidate)
        return (reliability, producibility, scored.overall_score)

    best = max(candidates, key=sort_key)

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
