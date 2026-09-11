"""Asset Acquisition: finds and downloads (or synthesizes) a real visual
for every scene.

See CLAUDE.md pipeline stage 6. For each scene, queries every one of its
visual_search_queries against BOTH Pexels and Pixabay (video search first,
photo search as a fallback) -- not stopping at the first hit -- scores every
candidate found with visual_relevance.py (metadata-only; see that module's
docstring for why), and either downloads the best-scoring candidate or, if
nothing scores at or above RELEVANCE_THRESHOLD, renders a designed
information card instead of misleading generic footage (see info_card.py
and the task's explicit "never imply generic stock footage is footage of a
specific named game, GPU, laptop, or product" requirement).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from scripts.production.info_card import InfoCardError, render_info_card
from scripts.production.models import Scene
from scripts.production.providers.visual import AssetResult, VisualAssetProvider
from scripts.production.visual_relevance import (
    INFO_CARD_SHOT_TYPE,
    RELEVANCE_THRESHOLD,
    ScoredCandidate,
    score_candidate,
    select_best_candidate,
)
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Bounds API calls per scene: both Pexels and Pixabay have modest free-tier
# rate limits, so query collection stops once the pool is large enough to
# choose confidently from, rather than always exhausting every one of the
# 3-5 queries against both providers.
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
    queries = scene.visual_search_queries or [scene.narration_line]
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


def _select_asset(
    scene: Scene, providers: list[VisualAssetProvider], previous_shot_type: Optional[str]
) -> Optional[ScoredCandidate]:
    """Gathers candidates from every query against both providers (video
    first, photo fallback), scores them, and returns the best one -- or
    None if nothing was found at all. A below-threshold-but-found
    candidate is still returned (callers use it as an info-card backdrop
    rather than discarding it)."""
    video_candidates = _collect_candidates(scene, providers, "search_videos")
    scored = _score_all(video_candidates, scene, previous_shot_type)

    if not scored:
        photo_candidates = _collect_candidates(scene, providers, "search_photos")
        scored = _score_all(photo_candidates, scene, previous_shot_type)

    return select_best_candidate(scored)


def _download_best_candidate(
    scene: Scene, best: ScoredCandidate, providers: list[VisualAssetProvider], assets_dir: Path
) -> None:
    extension = "mp4" if best.asset.is_video else "jpg"
    dest_path = assets_dir / f"scene_{scene.index:02d}.{extension}"
    provider = next(p for p in providers if p.name == best.asset.provider)
    provider.download(best.asset, dest_path)

    scene.asset_path = dest_path
    scene.asset_is_video = best.asset.is_video
    scene.asset_source = best.asset.provider
    scene.asset_url = best.asset.page_url
    scene.asset_attribution = f"{best.asset.attribution} (relevance {best.score:.2f}: {best.reason})"


def _use_info_card(
    scene: Scene, best: Optional[ScoredCandidate], providers: list[VisualAssetProvider], assets_dir: Path
) -> None:
    """Renders a designed information card instead of a weak/missing match.

    If a below-threshold candidate exists, it is downloaded and used as a
    darkened backdrop (real, at-least-topically-adjacent visual context)
    rather than wasted -- see the task's example spec ("contextual
    background asset").
    """
    background_path = None
    background_is_video = False
    if best is not None:
        try:
            extension = "mp4" if best.asset.is_video else "jpg"
            backdrop_path = assets_dir / f"scene_{scene.index:02d}_backdrop.{extension}"
            provider = next(p for p in providers if p.name == best.asset.provider)
            provider.download(best.asset, backdrop_path)
            background_path = backdrop_path
            background_is_video = best.asset.is_video
        except Exception as exc:  # noqa: BLE001 -- a failed backdrop download must not block the info card itself
            logger.warning("Could not download backdrop for scene %d info card: %s", scene.index, exc)
            background_path = None

    headline = scene.on_screen_text or scene.narration_line.split(".")[0]
    key_fact = scene.narration_line if scene.on_screen_text else ""
    dest_path = assets_dir / f"scene_{scene.index:02d}.mp4"

    try:
        render_info_card(
            headline, key_fact, dest_path, background_path=background_path, background_is_video=background_is_video
        )
    except InfoCardError as exc:
        raise AssetAcquisitionError(f"Could not render information card for scene {scene.index}: {exc}") from exc

    scene.asset_path = dest_path
    scene.asset_is_video = True
    scene.asset_source = "info_card"
    scene.asset_url = None
    reason = (
        "no candidate met the relevance threshold"
        if best is None
        else f"best candidate scored {best.score:.2f} (below threshold {RELEVANCE_THRESHOLD})"
    )
    scene.asset_attribution = f"Generated information card ({reason})"


def acquire_assets(scenes: list[Scene], providers: list[VisualAssetProvider], workdir: Path) -> None:
    """Find (or synthesize) and set one asset per scene, mutating each Scene in place."""
    assets_dir = Path(workdir) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    previous_shot_type: Optional[str] = None
    for scene in scenes:
        best = _select_asset(scene, providers, previous_shot_type)

        if best is not None and best.score >= RELEVANCE_THRESHOLD:
            _download_best_candidate(scene, best, providers, assets_dir)
            previous_shot_type = best.shot_type
            logger.info(
                "Scene %d: acquired %s from %s (score=%.2f, shot_type=%s)",
                scene.index,
                "video" if best.asset.is_video else "photo",
                best.asset.provider,
                best.score,
                best.shot_type,
            )
        else:
            _use_info_card(scene, best, providers, assets_dir)
            previous_shot_type = INFO_CARD_SHOT_TYPE
            logger.info(
                "Scene %d: no sufficiently relevant asset found (best score=%s) -- used an information card instead",
                scene.index,
                f"{best.score:.2f}" if best is not None else "n/a",
            )
