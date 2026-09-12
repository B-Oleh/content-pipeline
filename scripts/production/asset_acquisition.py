"""Asset Acquisition: finds and downloads (or synthesizes) a real visual
for every scene.

See CLAUDE.md pipeline stage 6. For each scene, queries every one of its
effective visual search queries (scene.visual_search_queries expanded with
a few deterministic, intent-appropriate generic phrases -- see
scene_intent.py -- so a scene has a bigger, more targeted pool to search
before ever falling back to a text card) against BOTH Pexels and Pixabay
(video search first, photo search as a fallback) -- not stopping at the
first hit. Every candidate is first scored by visual_relevance.py (cheap,
metadata-only -- keyword/URL overlap, aspect ratio, shot-type variety)
purely to build a short, affordable shortlist; that metadata score is NOT
the final relevance decision (see visual_relevance.py's docstring update:
it proved semantically wrong on its own, e.g. matching a paper greeting
card to a "graphics card" scene on the shared word "card"). Duplicate
candidates (the same asset turned up by more than one query) are collapsed
by _dedupe_candidates() before the shortlist is ever built, so Gemini
Vision never sees the same thumbnail twice for no reason -- see
vision_validation.py's own SHORTLIST_SIZE/cache for the rest of that quota
story (real GitHub Actions runs have hit Gemini's free-tier request
quota). The shortlist is then sent to vision_validation.py, which uses
Gemini Vision to actually look at each candidate's thumbnail and makes the
real accept/reject call.

Every scene is given one of three production modes (see models.py's
PRODUCTION_MODE_* constants and docs/PRODUCTION_PIPELINE.md "Visual
production modes"):
- real_visual -- a strong, near-exact Vision-approved match, used as-is.
- hybrid_visual -- a genuinely honest but not exact/strong match, used as a real background with a
  small honest overlay card instead of shown full-screen as if exact.
- info_card -- no honest real or contextual visual was found at all; a
  designed, motion-enhanced text card is rendered instead of misleading
  generic footage (see info_card.py and the task's explicit "never imply
  generic stock footage is footage of a specific named game, GPU, laptop,
  or product" requirement).

Rejected candidates are never rescued to fill a scene. The pipeline checks
actual narration durations after acquisition, and retries another topic if
accepted media covers less than 80% or more than one info card is consecutive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from scripts.production.info_card import InfoCardError, render_hybrid_scene, render_info_card
from scripts.production.models import (
    PRODUCTION_MODE_HYBRID_VISUAL,
    PRODUCTION_MODE_INFO_CARD,
    PRODUCTION_MODE_REAL_VISUAL,
    Scene,
)
from scripts.production.providers.llm import LlmProvider
from scripts.production.providers.visual import AssetResult, VisualAssetProvider
from scripts.production.scene_intent import effective_queries_for_scene
from scripts.production.vision_validation import (
    EXACT_MATCH_SCORE,
    SHORTLIST_SIZE,
    VisionEvaluation,
    get_cached_evaluation,
    select_vision_validated_candidate,
)
from scripts.production.visual_relevance import (
    INFO_CARD_SHOT_TYPE,
    ScoredCandidate,
    score_candidate,
)
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Bounds API calls per scene: both Pexels and Pixabay have modest free-tier
# rate limits, so query collection stops once the pool is large enough to
# choose confidently from, rather than always exhausting every one of the
# (now up to scene_intent.MAX_QUERIES_PER_SCENE) queries against both
# providers.
MIN_CANDIDATE_POOL_SIZE = 6
RESULTS_PER_QUERY = 3


class AssetAcquisitionError(RuntimeError):
    """Raised when a scene's visual could not be acquired NOR synthesized
    (an information-card render failure) -- a below-threshold or
    completely-missing search result no longer raises this on its own,
    since those now fall back to a designed information card instead."""


def _collect_candidates(
    scene: Scene, providers: list[VisualAssetProvider], search_method: str
) -> list[tuple[AssetResult, str]]:
    candidates: list[tuple[AssetResult, str]] = []
    queries = effective_queries_for_scene(scene)
    for query in queries:
        for provider in providers:
            try:
                results = getattr(provider, search_method)(query, per_page=RESULTS_PER_QUERY)
            except Exception as exc:  # noqa: BLE001 -- one provider/query failing must not abort the scene
                logger.warning("%s failed on %s for %r: %s", search_method, provider.name, query, exc)
                continue
            candidates.extend((result, query) for result in results)
        if len(candidates) >= MIN_CANDIDATE_POOL_SIZE:
            break
    return candidates


def _score_all(
    candidates: list[tuple[AssetResult, str]], scene: Scene, previous_shot_type: Optional[str]
) -> list[ScoredCandidate]:
    return [score_candidate(asset, scene, query, previous_shot_type) for asset, query in candidates]


def _dedupe_candidates(scored: list[ScoredCandidate]) -> list[ScoredCandidate]:
    """Collapses duplicate candidates -- the same underlying asset returned
    by more than one search query -- to a single entry BEFORE any of them
    ever reaches Vision. This is deterministic (no API call) filtering, per
    the task's "prefer deterministic filtering before Vision" and "do not
    evaluate the same thumbnail twice" requirements: without this, a scene
    with several overlapping queries could otherwise fill its whole
    Vision shortlist with copies of the same one asset. Keeps the
    highest-metadata-scored occurrence of each distinct asset (identified
    by its thumbnail, falling back to its page URL if no thumbnail is
    available).
    """
    best_by_identity: dict[str, ScoredCandidate] = {}
    for candidate in scored:
        identity = candidate.asset.thumbnail_url or candidate.asset.page_url
        existing = best_by_identity.get(identity)
        if existing is None or candidate.score > existing.score:
            best_by_identity[identity] = candidate
    return list(best_by_identity.values())


def _shortlist_candidates(
    scene: Scene, providers: list[VisualAssetProvider], previous_shot_type: Optional[str]
) -> list[ScoredCandidate]:
    """Gathers candidates from every query against both providers (video
    first, photo fallback), scores them with the cheap metadata-only
    signal, deduplicates identical assets, and returns the top
    SHORTLIST_SIZE, best-first -- this is only a pre-filter to bound how
    many candidates get a real Vision call (see module docstring); it is
    NOT the final relevance decision."""
    video_candidates = _collect_candidates(scene, providers, "search_videos")
    scored = _score_all(video_candidates, scene, previous_shot_type)

    if not scored:
        photo_candidates = _collect_candidates(scene, providers, "search_photos")
        scored = _score_all(photo_candidates, scene, previous_shot_type)

    scored = _dedupe_candidates(scored)
    scored.sort(key=lambda candidate: candidate.score, reverse=True)
    return scored[:SHORTLIST_SIZE]


def _provider_for(candidate: ScoredCandidate, providers: list[VisualAssetProvider]) -> VisualAssetProvider:
    return next(p for p in providers if p.name == candidate.asset.provider)


def _download_best_candidate(
    scene: Scene, best: ScoredCandidate, providers: list[VisualAssetProvider], assets_dir: Path
) -> None:
    """Downloads `best` as-is and uses it directly as the scene's visual
    (scene.production_mode = "real_visual") -- for a strong, near-exact
    Vision-approved match only (see EXACT_MATCH_SCORE in
    vision_validation.py)."""
    extension = "mp4" if best.asset.is_video else "jpg"
    dest_path = assets_dir / f"scene_{scene.index:02d}.{extension}"
    _provider_for(best, providers).download(best.asset, dest_path)

    scene.asset_path = dest_path
    scene.asset_is_video = best.asset.is_video
    scene.asset_source = best.asset.provider
    scene.asset_url = best.asset.page_url
    scene.asset_attribution = f"{best.asset.attribution} (relevance {best.score:.2f}: {best.reason})"
    scene.media_accepted = True
    scene.production_mode = PRODUCTION_MODE_REAL_VISUAL


def _headline_and_fact(scene: Scene) -> tuple[str, str]:
    headline = scene.on_screen_text or scene.narration_line.split(".")[0]
    key_fact = scene.narration_line if scene.on_screen_text else ""
    return headline, key_fact


def _use_hybrid_scene(
    scene: Scene,
    candidate: ScoredCandidate,
    evaluation: VisionEvaluation,
    providers: list[VisualAssetProvider],
    assets_dir: Path,
) -> None:
    """Render approved contextual media with a small, honest overlay."""
    source_extension = "mp4" if candidate.asset.is_video else "jpg"
    source_path = assets_dir / f"scene_{scene.index:02d}_source.{source_extension}"
    _provider_for(candidate, providers).download(candidate.asset, source_path)

    dest_path = assets_dir / f"scene_{scene.index:02d}.mp4"
    headline, key_fact = _headline_and_fact(scene)
    try:
        render_hybrid_scene(source_path, candidate.asset.is_video, headline, key_fact, dest_path)
    except InfoCardError as exc:
        raise AssetAcquisitionError(f"Could not render hybrid scene for scene {scene.index}: {exc}") from exc
    finally:
        source_path.unlink(missing_ok=True)

    scene.asset_path = dest_path
    scene.asset_is_video = True
    scene.asset_source = candidate.asset.provider
    scene.asset_url = candidate.asset.page_url
    reason = "honest but not exact match"
    scene.asset_attribution = (
        f"{candidate.asset.attribution} (relevance {candidate.score:.2f}, vision score={evaluation.scene_relevance_score}: {reason})"
    )
    scene.media_accepted = True
    scene.production_mode = PRODUCTION_MODE_HYBRID_VISUAL


def _use_info_card(scene: Scene, had_candidates: bool, dest_path: Path) -> None:
    """Renders a designed information card with a plain flat/gradient
    background -- NEVER a rejected or otherwise unvalidated candidate's
    asset.

    A candidate Gemini Vision rejected (out of domain, misleading, or
    below the relevance threshold) must never appear anywhere in the
    rendered video -- not as scene footage, not as a photo, and not as an
    information-card background, blurred/darkened or otherwise (see the
    task's explicit correction: a grocery shelf rejected for a PC discount
    scene must never appear anywhere in that scene). There is therefore no
    backdrop download here at all; `render_info_card()`'s own
    `background_path=None` default renders its plain designed background
    (see info_card.py).
    """
    headline, key_fact = _headline_and_fact(scene)

    try:
        render_info_card(headline, key_fact, dest_path)
    except InfoCardError as exc:
        raise AssetAcquisitionError(f"Could not render information card for scene {scene.index}: {exc}") from exc

    scene.asset_path = dest_path
    scene.asset_is_video = True
    scene.asset_source = "info_card"
    scene.asset_url = None
    reason = "no candidate was found at all" if not had_candidates else "no shortlisted candidate passed Gemini Vision's relevance/domain check"
    scene.asset_attribution = f"Generated information card ({reason})"
    scene.media_accepted = False
    scene.production_mode = PRODUCTION_MODE_INFO_CARD


def acquire_assets(
    scenes: list[Scene], providers: list[VisualAssetProvider], workdir: Path, llm_provider: LlmProvider
) -> None:
    """Find (or synthesize) and set one asset per scene, mutating each Scene
    in place. `llm_provider` makes the final Vision-based relevance call
    over each scene's cheap metadata shortlist (see module docstring).

    `vision_cache` is created once here and threaded through every scene's
    Vision call so an identical thumbnail+context is never sent to Gemini
    twice within this run. Only Vision-approved candidates are downloaded.
    """
    assets_dir = Path(workdir) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    vision_cache: dict[tuple[str, str, Optional[str], str], VisionEvaluation] = {}

    previous_shot_type: Optional[str] = None
    consecutive_info_cards = 0
    for scene in scenes:
        shortlist = _shortlist_candidates(scene, providers, previous_shot_type)
        approved = select_vision_validated_candidate(llm_provider, shortlist, scene, cache=vision_cache) if shortlist else None

        if approved is None and any(c.asset.is_video for c in shortlist):
            photos = _score_all(_collect_candidates(scene, providers, "search_photos"), scene, previous_shot_type)
            photos = sorted(_dedupe_candidates(photos), key=lambda c: c.score, reverse=True)[:SHORTLIST_SIZE]
            approved = select_vision_validated_candidate(llm_provider, photos, scene, cache=vision_cache) if photos else None
            shortlist += photos

        if approved is not None:
            evaluation = get_cached_evaluation(approved, scene, vision_cache)
            score = evaluation.scene_relevance_score if evaluation is not None else EXACT_MATCH_SCORE

            if score >= EXACT_MATCH_SCORE:
                _download_best_candidate(scene, approved, providers, assets_dir)
                logger.info(
                    "Scene %d: acquired %s from %s (metadata_score=%.2f, shot_type=%s, vision_score=%d) -- real_visual",
                    scene.index,
                    "video" if approved.asset.is_video else "photo",
                    approved.asset.provider,
                    approved.score,
                    approved.shot_type,
                    score,
                )
            else:
                _use_hybrid_scene(scene, approved, evaluation, providers, assets_dir)
                logger.info(
                    "Scene %d: acquired honest but not exact match from %s (vision_score=%d) -- hybrid_visual",
                    scene.index,
                    approved.asset.provider,
                    score,
                )
            previous_shot_type = approved.shot_type
            consecutive_info_cards = 0
            continue

        dest_path = assets_dir / f"scene_{scene.index:02d}.mp4"
        _use_info_card(scene, had_candidates=bool(shortlist), dest_path=dest_path)
        previous_shot_type = INFO_CARD_SHOT_TYPE
        consecutive_info_cards += 1
        logger.info(
            "Scene %d: no Vision-approved asset found (%d candidate(s) shortlisted) -- used an information card instead (%d consecutive)",
            scene.index,
            len(shortlist),
            consecutive_info_cards,
        )
