"""Deterministic, metadata-only relevance scoring for candidate visual
assets (see task: "Improve scene-to-asset matching without adding new paid
APIs").

Scores each Pexels/Pixabay search result using only data already returned
by the search call itself (search query, the result's own page URL,
width/height) -- no image download, no vision-model call. This is the
"deterministic metadata/query scoring fallback" the task explicitly allows
in place of Gemini vision (real image download + a vision call adds real
complexity and, in this environment, cannot be verified against a live
API -- see docs/PRODUCTION_PIPELINE.md "Visual relevance"). It is
structured so a future vision-based scorer could be added as an additional
signal without changing asset_acquisition.py's calling contract: nothing
downstream cares how a ScoredCandidate's score was computed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from scripts.production.models import Scene
from scripts.production.providers.visual import AssetResult

# A candidate at or above this score is downloaded and used directly; below
# it (or no candidate at all), asset_acquisition.py uses a designed
# information card instead of misleading generic footage (see
# info_card.py and the task's explicit "never imply generic footage is
# footage of a specific named game/GPU/laptop/product" requirement).
RELEVANCE_THRESHOLD = 0.22

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "for", "with", "and", "or", "but", "this",
    "that", "these", "those", "it", "its", "your", "you", "we", "our",
    "at", "as", "by", "from", "up", "down", "into", "over", "under",
    "video", "footage", "stock", "clip", "clips", "free", "download",
    "hd", "4k", "royalty",
}

# Generic gaming-adjacent terms that, alone, are weak evidence of relevance
# to a SPECIFIC scene -- almost any gaming b-roll matches these (see task's
# explicit "unless it actually supports the scene" requirement).
_GENERIC_TERMS = {
    "rgb", "gaming", "gamer", "gamers", "keyboard", "keyboards", "monitor",
    "monitors", "computer", "computers", "pc", "pcs", "technology", "tech",
    "screen", "screens", "desk", "setup",
}

# Used both to score relevance and to classify a "shot type" for variety
# tracking (see select_best_candidate's caller in asset_acquisition.py).
_SHOT_TYPE_KEYWORDS: dict[str, set[str]] = {
    "close_up": {"close", "closeup", "macro", "detail", "zoom"},
    "hardware_detail": {"inside", "case", "component", "chip", "gpu", "cpu", "motherboard", "circuit", "hardware", "build"},
    "monitor_ui": {"screen", "monitor", "display", "ui", "menu", "settings", "interface", "software"},
    "person_use_case": {"person", "hand", "hands", "typing", "playing", "gamer", "man", "woman", "player"},
    "environment_setup": {"setup", "desk", "room", "environment", "office", "workspace"},
}
DEFAULT_SHOT_TYPE = "environment_setup"
INFO_CARD_SHOT_TYPE = "graphic_text"

# Soft nudge away from repeating the same shot type as the immediately
# preceding scene (see task's "avoid repeating the same type of shot scene
# after scene") -- never rejects an otherwise strong match purely for
# variety, only tie-breaks between similarly-relevant candidates.
_REPEATED_SHOT_TYPE_PENALTY = 0.08

# Assets that crop to close to the target 9:16 lose less content to
# cropping -- a small, metadata-only quality signal.
_TARGET_ASPECT_RATIO = 1080 / 1920
_ASPECT_BONUS_WEIGHT = 0.15


@dataclass
class ScoredCandidate:
    asset: AssetResult
    query: str
    score: float
    shot_type: str
    reason: str


def extract_keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {word for word in words if len(word) > 2 and word not in _STOPWORDS}


def classify_shot_type(*texts: str) -> str:
    combined = extract_keywords(" ".join(t for t in texts if t))
    best_type = DEFAULT_SHOT_TYPE
    best_overlap = 0
    for shot_type, keywords in _SHOT_TYPE_KEYWORDS.items():
        overlap = len(combined & keywords)
        if overlap > best_overlap:
            best_overlap = overlap
            best_type = shot_type
    return best_type


def score_candidate(
    asset: AssetResult,
    scene: Scene,
    query: str,
    previous_shot_type: Optional[str] = None,
) -> ScoredCandidate:
    """Score how likely `asset` genuinely matches `scene`'s intent, using
    only search-result metadata -- see module docstring for why there is no
    image download/vision call here yet.
    """
    scene_keywords = extract_keywords(scene.narration_line) | extract_keywords(query)
    specific_keywords = scene_keywords - _GENERIC_TERMS

    url_keywords = extract_keywords(asset.page_url)
    matched = specific_keywords & url_keywords

    if specific_keywords:
        score = len(matched) / len(specific_keywords)
    else:
        # Nothing but generic terms to go on -- a weak, not zero, match:
        # claiming false precision either way would be worse than being
        # honestly uncertain.
        score = 0.1

    if asset.width and asset.height:
        aspect_ratio = asset.width / asset.height
        aspect_closeness = max(0.0, 1.0 - abs(aspect_ratio - _TARGET_ASPECT_RATIO))
        score += _ASPECT_BONUS_WEIGHT * aspect_closeness

    shot_type = classify_shot_type(query, asset.page_url)
    if previous_shot_type is not None and shot_type == previous_shot_type:
        score -= _REPEATED_SHOT_TYPE_PENALTY

    score = max(0.0, round(score, 3))

    if matched:
        reason = f"matched specific keyword(s) {sorted(matched)} from query {query!r}"
    else:
        reason = f"no specific keyword overlap for query {query!r} (metadata-only match)"

    return ScoredCandidate(asset=asset, query=query, score=score, shot_type=shot_type, reason=reason)


def select_best_candidate(candidates: list[ScoredCandidate]) -> Optional[ScoredCandidate]:
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.score)
