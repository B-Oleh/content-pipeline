"""Asset Acquisition: finds and downloads a real visual for every scene.

See CLAUDE.md pipeline stage 6. For each scene, tries each of its
visual_search_queries against video search first (Pexels, then Pixabay),
then falls back to photo search on the same two providers if no usable
video is found anywhere. This never fabricates footage of a specific
product/game -- see docs/PRODUCTION_PIPELINE.md "Topic selection" and
"Visuals" for why queries are generic/contextual by construction (Script
Agent is instructed to write them that way).
"""

from __future__ import annotations

from pathlib import Path

from scripts.production.models import Scene
from scripts.production.providers.visual import AssetResult, VisualAssetProvider
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)


class AssetAcquisitionError(RuntimeError):
    """Raised when no usable visual (video or photo) could be found for a scene."""


def _find_asset_for_scene(scene: Scene, providers: list[VisualAssetProvider]) -> AssetResult:
    # Prefer video over static images across every provider/query first, per
    # CLAUDE.md/task requirement -- fall back to photos only if genuinely no
    # video result exists anywhere for this scene.
    for query in scene.visual_search_queries or [scene.narration_line]:
        for provider in providers:
            try:
                results = provider.search_videos(query)
            except Exception as exc:  # noqa: BLE001 -- one provider/query failing must not abort the scene
                logger.warning("Video search failed on %s for %r: %s", provider.name, query, exc)
                continue
            if results:
                return results[0]

    for query in scene.visual_search_queries or [scene.narration_line]:
        for provider in providers:
            try:
                results = provider.search_photos(query)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Photo search failed on %s for %r: %s", provider.name, query, exc)
                continue
            if results:
                return results[0]

    raise AssetAcquisitionError(
        f"No usable video or photo found for scene {scene.index} (queries tried: {scene.visual_search_queries})"
    )


def acquire_assets(scenes: list[Scene], providers: list[VisualAssetProvider], workdir: Path) -> None:
    """Find and download one asset per scene, mutating each Scene in place."""
    assets_dir = Path(workdir) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    for scene in scenes:
        asset = _find_asset_for_scene(scene, providers)
        extension = "mp4" if asset.is_video else "jpg"
        dest_path = assets_dir / f"scene_{scene.index:02d}.{extension}"

        provider = next(p for p in providers if p.name == asset.provider)
        provider.download(asset, dest_path)

        scene.asset_path = dest_path
        scene.asset_is_video = asset.is_video
        scene.asset_source = asset.provider
        scene.asset_url = asset.page_url
        scene.asset_attribution = asset.attribution
        logger.info(
            "Scene %d: acquired %s asset from %s (%s)",
            scene.index,
            "video" if asset.is_video else "photo",
            asset.provider,
            asset.page_url,
        )
