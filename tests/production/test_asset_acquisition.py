"""asset_acquisition.py tests, using fake in-memory VisualAssetProvider
implementations -- no real network calls (see CLAUDE.md "Testing
requirements": stage tests should be runnable without live credentials).

The below-threshold path renders a real information card (info_card.py),
which needs real ffmpeg -- see test_info_card.py for why this is skipped
rather than mocked when ffmpeg/ffprobe are unavailable. "Downloaded" fake
assets are real, tiny, ffmpeg-generated files (not literal garbage bytes)
so a below-threshold candidate can be genuinely used as an info-card
backdrop, exactly like a real downloaded asset would be.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.production.asset_acquisition import acquire_assets
from scripts.production.models import Scene
from scripts.production.providers.visual import AssetResult, VisualAssetProvider
from scripts.production.visual_relevance import RELEVANCE_THRESHOLD

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed -- the below-threshold path renders a real info card",
)


def _asset(provider: str, page_url: str, *, is_video: bool = True, width: int = 1080, height: int = 1920) -> AssetResult:
    return AssetResult(
        provider=provider,
        page_url=page_url,
        download_url=f"https://cdn.example.com/{provider}/{page_url.rsplit('/', 1)[-1]}",
        width=width,
        height=height,
        is_video=is_video,
        duration_seconds=10.0 if is_video else None,
        attribution=f"Asset by Someone on {provider}",
    )


class _FakeProvider(VisualAssetProvider):
    """Returns pre-programmed results per query, records downloads, never hits the network."""

    def __init__(self, name: str, video_results: dict[str, list[AssetResult]], photo_results: dict[str, list[AssetResult]] | None = None) -> None:
        self.name = name
        self._video_results = video_results
        self._photo_results = photo_results or {}
        self.downloaded: list[tuple[AssetResult, Path]] = []

    def search_videos(self, query: str, per_page: int = 5) -> list[AssetResult]:
        return self._video_results.get(query, [])

    def search_photos(self, query: str, per_page: int = 5) -> list[AssetResult]:
        return self._photo_results.get(query, [])

    def download(self, asset: AssetResult, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if asset.is_video:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2", "-pix_fmt", "yuv420p", str(dest_path)],
                capture_output=True, text=True, timeout=30, check=True,
            )
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180", "-frames:v", "1", str(dest_path)],
                capture_output=True, text=True, timeout=30, check=True,
            )
        self.downloaded.append((asset, dest_path))


def _scene(index: int, narration: str, queries: list[str]) -> Scene:
    return Scene(index=index, narration_line=narration, visual_search_queries=queries)


def test_acquires_the_best_scoring_candidate_across_both_providers(tmp_path):
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": [_asset("pexels", "https://pexels.com/video/ocean-waves-1")]})
    pixabay = _FakeProvider(
        "pixabay", {"desktop gpu inside pc case": [_asset("pixabay", "https://pixabay.com/video/desktop-gpu-inside-pc-case-2")]}
    )

    acquire_assets([scene], [pexels, pixabay], tmp_path)

    assert scene.asset_source == "pixabay"
    assert scene.asset_is_video is True
    assert scene.asset_path is not None and scene.asset_path.exists()
    assert len(pixabay.downloaded) == 1
    assert len(pexels.downloaded) == 0


def test_queries_multiple_search_terms_not_just_the_first(tmp_path):
    scene = _scene(
        0,
        "A desktop GPU inside a PC case",
        ["irrelevant query one", "irrelevant query two", "desktop gpu inside pc case"],
    )
    pexels = _FakeProvider(
        "pexels",
        {
            "irrelevant query one": [_asset("pexels", "https://pexels.com/video/beach-sunset-1")],
            "desktop gpu inside pc case": [_asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1")],
        },
    )

    acquire_assets([scene], [pexels], tmp_path)

    assert scene.asset_url == "https://pexels.com/video/desktop-gpu-inside-pc-case-1"


def test_falls_back_to_an_information_card_when_nothing_meets_the_threshold(tmp_path):
    scene = _scene(0, "A very specific named product review", ["specific named product review"])
    pexels = _FakeProvider("pexels", {"specific named product review": [_asset("pexels", "https://pexels.com/video/random-unrelated-topic-1")]})

    acquire_assets([scene], [pexels], tmp_path)

    assert scene.asset_source == "info_card"
    assert scene.asset_is_video is True
    assert scene.asset_path is not None and scene.asset_path.exists()
    assert scene.asset_url is None


def test_falls_back_to_an_information_card_when_provider_returns_nothing(tmp_path):
    scene = _scene(0, "Some narration", ["a query with no hits"])
    pexels = _FakeProvider("pexels", {})

    acquire_assets([scene], [pexels], tmp_path)

    assert scene.asset_source == "info_card"
    assert scene.asset_path is not None and scene.asset_path.exists()


def test_falls_back_to_photo_search_when_video_search_finds_nothing(tmp_path):
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider(
        "pexels",
        video_results={},
        photo_results={"desktop gpu inside pc case": [_asset("pexels", "https://pexels.com/photo/desktop-gpu-inside-pc-case-1", is_video=False)]},
    )

    acquire_assets([scene], [pexels], tmp_path)

    assert scene.asset_source == "pexels"
    assert scene.asset_is_video is False


def test_tracks_shot_type_variety_across_scenes(tmp_path):
    scene_a = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    scene_b = _scene(1, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider(
        "pexels",
        {
            "desktop gpu inside pc case": [
                _asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1"),
                _asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-2"),
            ]
        },
    )

    acquire_assets([scene_a, scene_b], [pexels], tmp_path)

    # Both scenes still get real, relevant assets -- the variety penalty is a
    # soft tie-breaker, not a hard rejection (see visual_relevance.py).
    assert scene_a.asset_source == "pexels"
    assert scene_b.asset_source == "pexels"


def test_info_card_uses_a_weak_candidate_as_a_backdrop_instead_of_discarding_it(tmp_path):
    scene = _scene(0, "Should you buy a mechanical keyboard with RGB lighting for gaming", ["rgb gaming keyboard monitor"])
    pexels = _FakeProvider("pexels", {"rgb gaming keyboard monitor": [_asset("pexels", "https://pexels.com/video/rgb-gaming-keyboard-monitor-1")]})

    acquire_assets([scene], [pexels], tmp_path)

    assert scene.asset_source == "info_card"
    # The weak candidate should still have been downloaded, as a backdrop.
    assert len(pexels.downloaded) == 1


def test_relevance_threshold_is_respected_end_to_end(tmp_path):
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": [_asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1")]})

    acquire_assets([scene], [pexels], tmp_path)

    assert scene.asset_source != "info_card"
    assert scene.asset_attribution is not None
    reported_score = float(scene.asset_attribution.split("relevance ")[1].split(":")[0])
    assert reported_score >= RELEVANCE_THRESHOLD
