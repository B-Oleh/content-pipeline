"""Gemini Vision-based semantic relevance validation for candidate visual
assets.

This is the fix for a real failure observed in produced video output:
visual_relevance.py's metadata/keyword scoring matched a paper greeting
card to a "graphics card" scene (both share the word "card") and a
grocery-store shelf to a "discount" scene (both matched generic
sale/deal-adjacent keywords). Keyword/URL overlap alone cannot tell a
paper card from a GPU, or a grocery shelf from a PC hardware sale display
-- it has no notion of what is actually IN the image. See
asset_acquisition.py for how this module is wired in: visual_relevance.py's
score_candidate() still produces a cheap, free, metadata-only shortlist
(the top few candidates worth spending a real Vision call on); Vision then
makes the final accept/reject call by actually looking at a thumbnail.

Only a small preview/thumbnail image is ever sent to Vision or downloaded
for evaluation (see providers/visual.py's AssetResult.thumbnail_url) --
never the full video/photo file, which is only downloaded after a
candidate has already passed this check (see asset_acquisition.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Optional

import requests

from scripts.production.models import Scene
from scripts.production.providers.llm import LlmProvider, VisionEvaluationError
from scripts.production.visual_relevance import ScoredCandidate
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Every vision prompt explicitly anchors the model to this domain (see task's
# "never evaluate ambiguous words ... without PC/gaming context" requirement)
# -- words like "card", "memory", "storage", "case", "discount", "deal",
# "performance", "power" are meaningless as relevance signals without it.
CONTENT_DOMAIN_DESCRIPTION = "PC gaming, gaming hardware, computer components, gaming technology, or game recommendations"

# A candidate must score at or above this AND be in-domain AND not
# misleading to be used; anything else falls back to an information card
# (see asset_acquisition.py) rather than using the best-of-a-bad-lot result.
VISION_RELEVANCE_THRESHOLD = 70

# Only the top few metadata-scored candidates are ever sent to Vision, to
# keep the number of Gemini calls per scene small (see task's "avoid using
# Gemini Vision on excessive candidates" requirement) -- metadata scoring
# already did the cheap first-pass ranking in visual_relevance.py.
SHORTLIST_SIZE = 3

THUMBNAIL_TIMEOUT_SECONDS = 15.0
DEFAULT_THUMBNAIL_MIME_TYPE = "image/jpeg"


@dataclass
class VisionEvaluation:
    computer_domain: bool
    scene_relevance_score: int
    misleading: bool
    reason: str

    @property
    def passed(self) -> bool:
        """The three hard-rejection rules from the task, combined: out of
        domain, below threshold, or misleading all reject -- there is no
        partial credit."""
        return self.computer_domain and not self.misleading and self.scene_relevance_score >= VISION_RELEVANCE_THRESHOLD


def _guess_mime_type(url: str) -> str:
    lowered = url.lower()
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith(".webp"):
        return "image/webp"
    return DEFAULT_THUMBNAIL_MIME_TYPE


def fetch_thumbnail_bytes(url: str, timeout: float = THUMBNAIL_TIMEOUT_SECONDS) -> tuple[bytes, str]:
    """Downloads only a small preview image for evaluation -- never the
    full video/photo file (see module docstring)."""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content, _guess_mime_type(url)


def build_vision_prompt(scene: Scene, query: str) -> str:
    """Pure function (no SDK dependency), directly unit testable -- mirrors
    providers/llm.py::build_script_prompt's pattern."""
    narration = scene.narration_line
    intent = scene.on_screen_text or narration
    return (
        f"The content domain is {CONTENT_DOMAIN_DESCRIPTION}.\n\n"
        "You are validating whether a candidate stock photo/video thumbnail genuinely, visibly "
        "matches a specific video scene in this domain -- not just a loose keyword association.\n\n"
        f"Scene narration: {narration!r}\n"
        f"Scene intent / on-screen text: {intent!r}\n"
        f"Search query used to find this candidate: {query!r}\n\n"
        "Look at the attached image and answer honestly. Ambiguous words like 'card', 'memory', "
        "'storage', 'case', 'discount', 'deal', 'performance', or 'power' only count as relevant if "
        "the image ACTUALLY shows PC/gaming hardware, software, or a clearly PC-gaming-related "
        "retail/technology context -- not their unrelated everyday meaning.\n\n"
        "Reject (computer_domain=false and/or misleading=true) if the image shows: a paper "
        "greeting, business, ID, or bank/credit card; a grocery store shelf or food/groceries; "
        "medicine; cars; finance/investing imagery unrelated to hardware; travel; office paperwork; "
        "generic smartphone footage when the scene is about PC hardware; or a person with no "
        "visible computer/gaming relevance.\n\n"
        "Respond with ONLY a single JSON object of this exact shape (no markdown fences, no "
        "commentary):\n"
        '{"computer_domain": true or false, "scene_relevance_score": integer 0-100, '
        '"misleading": true or false, "reason": "one short sentence"}'
    )


def parse_vision_response(raw_text: str) -> dict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisionEvaluationError(f"Gemini Vision did not return valid JSON: {exc}") from exc

    required = ("computer_domain", "scene_relevance_score", "misleading")
    missing = [field for field in required if field not in data]
    if missing:
        raise VisionEvaluationError(f"Gemini Vision response is missing required field(s): {missing}")
    return data


def evaluate_candidate(provider: LlmProvider, image_bytes: bytes, mime_type: str, scene: Scene, query: str) -> VisionEvaluation:
    prompt = build_vision_prompt(scene, query)
    raw_text = provider.evaluate_visual_candidate(image_bytes, mime_type, prompt)
    data = parse_vision_response(raw_text)
    return VisionEvaluation(
        computer_domain=bool(data["computer_domain"]),
        scene_relevance_score=int(data["scene_relevance_score"]),
        misleading=bool(data["misleading"]),
        reason=str(data.get("reason", "")),
    )


def select_vision_validated_candidate(
    provider: LlmProvider, shortlisted: list[ScoredCandidate], scene: Scene
) -> Optional[ScoredCandidate]:
    """Vision makes the final semantic call; `shortlisted` is assumed
    already ordered best-metadata-score-first (see asset_acquisition.py),
    but that metadata order does NOT decide the winner here.

    Every shortlisted candidate is evaluated (not just until the first one
    passes), and among every candidate that passes Vision's hard rules
    (computer_domain, misleading, VISION_RELEVANCE_THRESHOLD), the one with
    the HIGHEST scene_relevance_score is returned -- e.g. a 72 does not win
    over a 94 just because it was checked first.

    Returns None if every shortlisted candidate is rejected (by Vision, or
    because no thumbnail was available to evaluate at all). A rejected
    candidate is NEVER returned here in any form -- callers must not reuse
    a rejected candidate's asset anywhere in the rendered video (not as
    footage, not as an information-card background); see
    asset_acquisition.py, which renders a plain/flat information card with
    no asset at all when this returns None.
    """
    approved: list[tuple[ScoredCandidate, VisionEvaluation]] = []

    for candidate in shortlisted:
        thumbnail_url = candidate.asset.thumbnail_url
        if not thumbnail_url:
            logger.warning("No thumbnail URL for candidate %s -- cannot Vision-validate, rejecting", candidate.asset.page_url)
            continue
        try:
            image_bytes, mime_type = fetch_thumbnail_bytes(thumbnail_url)
            evaluation = evaluate_candidate(provider, image_bytes, mime_type, scene, candidate.query)
        except Exception as exc:  # noqa: BLE001 -- one candidate's Vision failure must not abort the scene's search
            logger.warning("Vision evaluation failed for %s: %s", candidate.asset.page_url, exc)
            continue

        if evaluation.passed:
            logger.info(
                "Vision APPROVED %s (score=%d, domain=%s): %s",
                candidate.asset.page_url, evaluation.scene_relevance_score, evaluation.computer_domain, evaluation.reason,
            )
            approved.append((candidate, evaluation))
        else:
            logger.info(
                "Vision REJECTED %s (score=%d, domain=%s, misleading=%s): %s",
                candidate.asset.page_url, evaluation.scene_relevance_score, evaluation.computer_domain, evaluation.misleading, evaluation.reason,
            )

    if not approved:
        return None

    best_candidate, best_evaluation = max(approved, key=lambda pair: pair[1].scene_relevance_score)
    return replace(
        best_candidate,
        reason=f"{best_candidate.reason}; vision approved (score={best_evaluation.scene_relevance_score}): {best_evaluation.reason}",
    )
