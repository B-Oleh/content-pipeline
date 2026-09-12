"""asset_acquisition.py tests, using fake in-memory VisualAssetProvider and
LlmProvider (Vision) implementations -- no real network calls (see
CLAUDE.md "Testing requirements": stage tests should be runnable without
live credentials).

The info-card fallback path renders a real information card
(info_card.py), which needs real ffmpeg -- see test_info_card.py for why
this whole file is skipped rather than mocked when ffmpeg/ffprobe are
unavailable. "Downloaded" fake assets are real, tiny, ffmpeg-generated
files (not literal garbage bytes) so a candidate can be genuinely used as
an info-card backdrop, exactly like a real downloaded asset would be.
Thumbnail fetches (vision_validation.fetch_thumbnail_bytes) are mocked at
the `requests.get` level -- see _patch_thumbnail_fetch -- since those are
plain, unauthenticated HTTP GETs, not provider API calls.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.production.asset_acquisition import _dedupe_candidates, acquire_assets
from scripts.production.visual_relevance import ScoredCandidate
from scripts.production.models import (
    PRODUCTION_MODE_HYBRID_VISUAL,
    PRODUCTION_MODE_INFO_CARD,
    PRODUCTION_MODE_REAL_VISUAL,
    Scene,
)
from scripts.production.providers.llm import LlmProvider, VisionEvaluationError
from scripts.production.providers.visual import AssetResult, VisualAssetProvider
from scripts.production.vision_validation import EXACT_MATCH_SCORE, SHORTLIST_SIZE, VISION_RELEVANCE_THRESHOLD

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed -- the info-card fallback path renders a real info card",
)


_UNSET = object()


def _asset(
    provider: str, page_url: str, *, is_video: bool = True, width: int = 1080, height: int = 1920, thumbnail_url=_UNSET
) -> AssetResult:
    slug = page_url.rsplit("/", 1)[-1]
    return AssetResult(
        provider=provider,
        page_url=page_url,
        download_url=f"https://cdn.example.com/{provider}/{slug}",
        width=width,
        height=height,
        is_video=is_video,
        duration_seconds=10.0 if is_video else None,
        attribution=f"Asset by Someone on {provider}",
        thumbnail_url=f"https://cdn.example.com/{provider}/{slug}_thumb.jpg" if thumbnail_url is _UNSET else thumbnail_url,
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


class _FakeLlmProvider(LlmProvider):
    """Returns pre-programmed Vision evaluations in call order -- no Gemini
    SDK, no network. Raises if asked for more evaluations than programmed,
    so a test that expects N vision calls fails loudly if the code makes
    more than N."""

    def __init__(self, vision_responses: list[dict] | None = None) -> None:
        self._responses = list(vision_responses or [])
        self.vision_calls: list[dict] = []

    def generate_script(self, prompt: str) -> str:
        raise NotImplementedError

    def evaluate_visual_candidate(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        self.vision_calls.append({"image_bytes": image_bytes, "mime_type": mime_type, "prompt": prompt})
        if not self._responses:
            raise VisionEvaluationError("test ran out of programmed Vision responses")
        return json.dumps(self._responses.pop(0))


def _approve(score: int = 90, reason: str = "clearly shows PC hardware matching the scene") -> dict:
    return {"computer_domain": True, "scene_relevance_score": score, "misleading": False, "reason": reason}


def _reject(reason: str = "not relevant", *, computer_domain: bool = False, score: int = 10, misleading: bool = False) -> dict:
    return {"computer_domain": computer_domain, "scene_relevance_score": score, "misleading": misleading, "reason": reason}


def _scene(index: int, narration: str, queries: list[str]) -> Scene:
    return Scene(index=index, narration_line=narration, visual_search_queries=queries)


class _FakeThumbnailResponse:
    def __init__(self, content: bytes = b"fake-thumbnail-bytes") -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


def _patch_thumbnail_fetch(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.production.vision_validation.requests.get",
        lambda url, timeout=None: _FakeThumbnailResponse(),
    )


def test_acquires_the_best_metadata_candidate_once_vision_approves_it(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": [_asset("pexels", "https://pexels.com/video/ocean-waves-1")]})
    pixabay = _FakeProvider(
        "pixabay", {"desktop gpu inside pc case": [_asset("pixabay", "https://pixabay.com/video/desktop-gpu-inside-pc-case-2")]}
    )
    # Every shortlisted candidate is evaluated now (not just until the
    # first pass), so both candidates need a programmed response -- pixabay
    # (better metadata match, evaluated first) approved, pexels rejected.
    llm = _FakeLlmProvider([_approve(score=90), _reject("unrelated ocean footage")])

    acquire_assets([scene], [pexels, pixabay], tmp_path, llm)

    # pixabay's page URL keyword-matches the scene/query better -- that
    # metadata pre-filter still determines shortlist order.
    assert scene.asset_source == "pixabay"
    assert scene.asset_is_video is True
    assert scene.asset_path is not None and scene.asset_path.exists()
    assert len(pixabay.downloaded) == 1
    assert len(pexels.downloaded) == 0
    assert len(llm.vision_calls) == 2


def test_queries_multiple_search_terms_not_just_the_first(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
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
    llm = _FakeLlmProvider([_approve()])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_url == "https://pexels.com/video/desktop-gpu-inside-pc-case-1"


def test_vision_rejection_falls_back_to_an_information_card(tmp_path, monkeypatch):
    """The real failure this fixes: metadata scoring alone can be fooled --
    Vision is the one that actually looks at the image and rejects it."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A very specific named product review", ["specific named product review"])
    pexels = _FakeProvider("pexels", {"specific named product review": [_asset("pexels", "https://pexels.com/video/random-unrelated-topic-1")]})
    llm = _FakeLlmProvider([_reject("not PC/gaming related")])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"
    assert scene.asset_is_video is True
    assert scene.asset_path is not None and scene.asset_path.exists()
    assert scene.asset_url is None
    assert len(llm.vision_calls) == 1


def test_paper_card_candidate_is_rejected_for_a_gpu_scene(tmp_path, monkeypatch):
    """Real production failure this fixes: a paper greeting/business card
    matched a "graphics card" scene on the shared word "card" under
    metadata-only scoring. Vision (mocked here) correctly identifies the
    thumbnail as a paper card, not a GPU, and rejects it."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "This graphics card delivers a huge performance boost", ["PC graphics card GPU close up"])
    pexels = _FakeProvider("pexels", {"PC graphics card GPU close up": [_asset("pexels", "https://pexels.com/photo/greeting-card-1", is_video=False)]})
    llm = _FakeLlmProvider([_reject("the image shows a paper greeting card, not a GPU", computer_domain=False, score=5)])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"


def test_grocery_shelf_candidate_is_rejected_for_a_discount_scene(tmp_path, monkeypatch):
    """Real production failure this fixes: a grocery-store shelf (jam)
    matched a "discounts" scene on generic sale-adjacent keywords."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "Here are today's best PC hardware discounts", ["PC hardware sale gaming electronics store"])
    pexels = _FakeProvider(
        "pexels", {"PC hardware sale gaming electronics store": [_asset("pexels", "https://pexels.com/photo/grocery-store-shelf-jam-1", is_video=False)]}
    )
    llm = _FakeLlmProvider([_reject("the image shows a grocery store shelf with food products, not PC hardware", computer_domain=False, score=8)])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"


def test_pc_gpu_candidate_can_pass(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "This graphics card delivers a huge performance boost", ["computer graphics card installed in gaming PC"])
    pexels = _FakeProvider(
        "pexels", {"computer graphics card installed in gaming PC": [_asset("pexels", "https://pexels.com/video/gpu-installed-in-pc-case-1")]}
    )
    llm = _FakeLlmProvider([_approve(score=95, reason="a GPU clearly installed in a PC case")])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "pexels"
    assert "vision approved" in scene.asset_attribution


def test_candidate_below_vision_threshold_falls_back_to_info_card(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": [_asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1")]})
    # In domain, not misleading, but scored under the 70 threshold -- still a hard reject.
    llm = _FakeLlmProvider([_reject("a PC case is visible but no GPU", computer_domain=True, score=40)])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"


def test_falls_back_to_an_information_card_when_provider_returns_nothing(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "Some narration", ["a query with no hits"])
    pexels = _FakeProvider("pexels", {})
    llm = _FakeLlmProvider([])  # Vision must never be called -- nothing to evaluate.

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"
    assert scene.asset_path is not None and scene.asset_path.exists()
    assert len(llm.vision_calls) == 0


def test_falls_back_to_photo_search_when_video_search_finds_nothing(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider(
        "pexels",
        video_results={},
        photo_results={"desktop gpu inside pc case": [_asset("pexels", "https://pexels.com/photo/desktop-gpu-inside-pc-case-1", is_video=False)]},
    )
    llm = _FakeLlmProvider([_approve()])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "pexels"
    assert scene.asset_is_video is False


def test_no_thumbnail_url_is_rejected_without_a_vision_call(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    asset = _asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1", thumbnail_url=None)
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": [asset]})
    llm = _FakeLlmProvider([])  # Vision must never be called -- there is no thumbnail to send it.

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"
    assert len(llm.vision_calls) == 0


def test_vision_call_count_is_bounded_by_shortlist_size(tmp_path, monkeypatch):
    """See task's "avoid using Gemini Vision on excessive candidates"
    requirement -- even with more raw search hits than SHORTLIST_SIZE,
    Vision is never called more than SHORTLIST_SIZE times for one scene."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    many_candidates = [_asset("pexels", f"https://pexels.com/video/desktop-gpu-inside-pc-case-{i}") for i in range(6)]
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": many_candidates})
    # Every one of them rejected -- forces Vision to be asked about every
    # shortlisted candidate, proving the shortlist (not the raw pool) bounds the call count.
    llm = _FakeLlmProvider([_reject() for _ in range(SHORTLIST_SIZE)])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"
    assert len(llm.vision_calls) == SHORTLIST_SIZE


def test_tracks_shot_type_variety_across_scenes(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
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
    # Both candidates in each scene's 2-candidate shortlist are now
    # evaluated (not just until the first pass) -- 2 scenes x 2 candidates.
    llm = _FakeLlmProvider([_approve(), _approve(), _approve(), _approve()])

    acquire_assets([scene_a, scene_b], [pexels], tmp_path, llm)

    # Both scenes still get real, Vision-approved assets -- the metadata
    # shot-type-variety penalty is only a shortlist tie-breaker, never a
    # hard rejection (see visual_relevance.py).
    assert scene_a.asset_source == "pexels"
    assert scene_b.asset_source == "pexels"


def test_rejected_candidate_is_never_downloaded_or_used_as_an_info_card_background(tmp_path, monkeypatch):
    """A candidate Gemini Vision rejects must never appear anywhere in the
    rendered video -- not as footage, not as an information-card
    background (blurred/darkened or otherwise). Since the candidate is
    rejected, it must never even be downloaded."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "Should you buy a mechanical keyboard with RGB lighting for gaming", ["rgb gaming keyboard monitor"])
    pexels = _FakeProvider("pexels", {"rgb gaming keyboard monitor": [_asset("pexels", "https://pexels.com/video/rgb-gaming-keyboard-monitor-1")]})
    llm = _FakeLlmProvider([_reject("generic gaming b-roll with no specific relevance to this scene")])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"
    # The rejected candidate must never be downloaded at all -- not as
    # footage, and not as a background for the information card.
    assert len(pexels.downloaded) == 0


def test_non_computer_domain_candidate_is_never_used_as_an_info_card_background(tmp_path, monkeypatch):
    """The exact real failure this fixes: a grocery-store shelf rejected
    for a PC hardware discount scene must never appear anywhere in that
    scene -- including as a darkened/blurred info-card backdrop."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "Here are today's best PC hardware discounts", ["PC hardware sale gaming electronics store"])
    pexels = _FakeProvider(
        "pexels", {"PC hardware sale gaming electronics store": [_asset("pexels", "https://pexels.com/photo/grocery-store-shelf-jam-1", is_video=False)]}
    )
    llm = _FakeLlmProvider([_reject("the image shows a grocery store shelf with food products, not PC hardware", computer_domain=False, score=8)])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"
    assert len(pexels.downloaded) == 0
    assert scene.asset_path is not None and scene.asset_path.exists()


def test_when_every_shortlisted_candidate_is_rejected_the_info_card_uses_no_asset_at_all(tmp_path, monkeypatch):
    """If no valid PC/gaming-domain candidate exists, the info card must
    use a clean generated background -- never any of the rejected assets."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    many_candidates = [_asset("pexels", f"https://pexels.com/video/desktop-gpu-inside-pc-case-{i}") for i in range(SHORTLIST_SIZE)]
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": many_candidates})
    llm = _FakeLlmProvider([_reject() for _ in range(SHORTLIST_SIZE)])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "info_card"
    assert len(pexels.downloaded) == 0
    assert scene.asset_path is not None and scene.asset_path.exists()


def test_dedupe_candidates_keeps_the_highest_scoring_occurrence_of_a_duplicate():
    same_asset = _asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1")
    other_asset = _asset("pexels", "https://pexels.com/video/unrelated-1")
    weak = ScoredCandidate(asset=same_asset, query="q1", score=0.2, shot_type="hardware_detail", reason="weak match")
    strong = ScoredCandidate(asset=same_asset, query="q2", score=0.9, shot_type="hardware_detail", reason="strong match")
    distinct = ScoredCandidate(asset=other_asset, query="q3", score=0.5, shot_type="environment_setup", reason="other")

    deduped = _dedupe_candidates([weak, strong, distinct])

    assert len(deduped) == 2
    kept = next(c for c in deduped if c.asset is same_asset)
    assert kept is strong


def test_duplicate_candidates_from_overlapping_queries_are_deduped_before_vision(tmp_path, monkeypatch):
    """Two different queries returning the exact same underlying asset
    (a realistic overlap between similar search phrases) must not cost two
    Vision calls -- deterministic dedup happens before Vision ever sees it
    (see asset_acquisition.py::_dedupe_candidates)."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case", "gpu installed in gaming pc"])
    same_asset_hit_twice = _asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1")
    pexels = _FakeProvider(
        "pexels",
        {
            "desktop gpu inside pc case": [same_asset_hit_twice],
            "gpu installed in gaming pc": [same_asset_hit_twice],
        },
    )
    llm = _FakeLlmProvider([_approve()])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "pexels"
    assert len(llm.vision_calls) == 1


def test_vision_cache_prevents_reevaluating_the_same_thumbnail_across_scenes(tmp_path, monkeypatch):
    """Two scenes that happen to share the exact same narration, on-screen
    text, query, and candidate thumbnail must reuse the first scene's
    Vision judgment for the second rather than asking Gemini again -- see
    vision_validation.py's cache-key docstring for why this is scoped to
    an exact thumbnail+context match, not thumbnail alone."""
    _patch_thumbnail_fetch(monkeypatch)
    identical_asset = _asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1")
    scene_a = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    scene_b = _scene(1, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": [identical_asset]})
    llm = _FakeLlmProvider([_approve()])

    acquire_assets([scene_a, scene_b], [pexels], tmp_path, llm)

    assert scene_a.asset_source == "pexels"
    assert scene_b.asset_source == "pexels"
    assert len(llm.vision_calls) == 1  # scene_b's identical candidate reused scene_a's cached evaluation


# ---------------------------------------------------------------------------
# Part 1/4/5/7: production modes, hybrid_visual, and the consecutive-info-
# card density rule.
# ---------------------------------------------------------------------------


def test_strong_match_is_recorded_as_real_visual(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A desktop GPU inside a PC case", ["desktop gpu inside pc case"])
    pexels = _FakeProvider("pexels", {"desktop gpu inside pc case": [_asset("pexels", "https://pexels.com/video/desktop-gpu-inside-pc-case-1")]})
    llm = _FakeLlmProvider([_approve(score=EXACT_MATCH_SCORE)])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.production_mode == PRODUCTION_MODE_REAL_VISUAL
    assert scene.asset_source == "pexels"


def test_info_card_fallback_is_recorded_as_info_card_mode(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A very specific named product review", ["specific named product review"])
    pexels = _FakeProvider("pexels", {"specific named product review": [_asset("pexels", "https://pexels.com/video/random-unrelated-topic-1")]})
    llm = _FakeLlmProvider([_reject("not PC/gaming related")])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.production_mode == PRODUCTION_MODE_INFO_CARD


def test_honest_but_not_exact_match_becomes_hybrid_visual(tmp_path, monkeypatch):
    """An approved candidate scoring between VISION_RELEVANCE_THRESHOLD and
    EXACT_MATCH_SCORE is genuinely relevant but not a strong/exact match --
    it must be rendered as a hybrid scene (real footage + overlay card),
    not shown full-screen as if it were exact."""
    _patch_thumbnail_fetch(monkeypatch)
    assert VISION_RELEVANCE_THRESHOLD < 75 < EXACT_MATCH_SCORE
    scene = _scene(0, "A gaming desk setup with RGB lighting", ["gaming desk setup"])
    pexels = _FakeProvider("pexels", {"gaming desk setup": [_asset("pexels", "https://pexels.com/video/gaming-desk-setup-1")]})
    llm = _FakeLlmProvider([_approve(score=75, reason="a genuinely relevant gaming desk setup, not an exact match")])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.production_mode == PRODUCTION_MODE_HYBRID_VISUAL
    assert scene.asset_source == "pexels"
    assert scene.asset_path is not None and scene.asset_path.exists()
    # The rendered hybrid scene replaces the raw download at the final
    # scene asset path -- the intermediate source download must not linger.
    assets_dir = tmp_path / "assets"
    remaining = sorted(p.name for p in assets_dir.iterdir())
    assert remaining == ["scene_00.mp4"]


def test_info_card_is_not_selected_when_an_honest_hybrid_candidate_exists(tmp_path, monkeypatch):
    """Part 7's explicit requirement: info_card must not be chosen if a
    valid real or hybrid candidate exists."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(0, "A gaming desk setup with RGB lighting", ["gaming desk setup"])
    pexels = _FakeProvider("pexels", {"gaming desk setup": [_asset("pexels", "https://pexels.com/video/gaming-desk-setup-1")]})
    llm = _FakeLlmProvider([_approve(score=75)])

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.production_mode != PRODUCTION_MODE_INFO_CARD
    assert scene.asset_source != "info_card"


def test_rejected_near_miss_is_never_used_even_after_consecutive_cards(tmp_path, monkeypatch):
    """The pipeline retries topics instead of using rejected media to break a streak."""
    _patch_thumbnail_fetch(monkeypatch)
    assert 60 < VISION_RELEVANCE_THRESHOLD
    scene_a = _scene(0, "Some narration with no visual hits at all", ["a query with no hits at all a"])
    scene_b = _scene(1, "Another narration with no visual hits at all", ["a query with no hits at all b"])
    scene_c = _scene(2, "A gaming desk setup nearby", ["gaming desk setup nearby"])
    pexels = _FakeProvider("pexels", {"gaming desk setup nearby": [_asset("pexels", "https://pexels.com/video/near-miss-1")]})
    llm = _FakeLlmProvider([_reject("in-domain but not a strong match", computer_domain=True, score=60, misleading=False)])

    acquire_assets([scene_a, scene_b, scene_c], [pexels], tmp_path, llm)

    assert scene_a.production_mode == PRODUCTION_MODE_INFO_CARD
    assert scene_b.production_mode == PRODUCTION_MODE_INFO_CARD
    assert scene_c.production_mode == PRODUCTION_MODE_INFO_CARD
    assert not scene_c.media_accepted
    assert not pexels.downloaded


def test_among_multiple_passing_candidates_the_highest_vision_score_is_selected(tmp_path, monkeypatch):
    """candidate A=72, candidate B=94, candidate C=rejected -> B must win,
    not the first one checked that merely clears the threshold."""
    _patch_thumbnail_fetch(monkeypatch)
    scene = _scene(
        0,
        "This graphics card delivers a huge performance boost",
        ["computer graphics card installed in gaming PC"],
    )
    candidates = [
        _asset("pexels", "https://pexels.com/video/candidate-a-72"),
        _asset("pexels", "https://pexels.com/video/candidate-b-94"),
        _asset("pexels", "https://pexels.com/video/candidate-c-rejected"),
    ]
    pexels = _FakeProvider("pexels", {"computer graphics card installed in gaming PC": candidates})
    llm = _FakeLlmProvider(
        [
            _approve(score=72, reason="a weaker but valid match"),
            _approve(score=94, reason="the strongest match"),
            _reject("not PC/gaming related"),
        ]
    )

    acquire_assets([scene], [pexels], tmp_path, llm)

    assert scene.asset_source == "pexels"
    assert scene.asset_url == "https://pexels.com/video/candidate-b-94"


def test_photo_fallback_after_vision_rejects_video(tmp_path, monkeypatch):
    _patch_thumbnail_fetch(monkeypatch)
    query = "gaming desk setup"
    scene = _scene(0, "A gaming desk setup", [query])
    video = _asset("pexels", "https://pexels.com/video/gaming-desk-video")
    photo = _asset("pexels", "https://pexels.com/photo/gaming-desk-photo", is_video=False)
    provider = _FakeProvider("pexels", {query: [video]}, {query: [photo]})
    llm = _FakeLlmProvider([_reject("not relevant"), _approve(score=95)])
    acquire_assets([scene], [provider], tmp_path, llm)
    assert scene.asset_url == photo.page_url
    assert not scene.asset_is_video
    assert scene.media_accepted
    assert [asset for asset, _ in provider.downloaded] == [photo]
