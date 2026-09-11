"""vision_validation.py unit tests -- fake LlmProvider, no network, no
Gemini SDK. Covers the exact real-world failures this module fixes (a
paper card matching a "graphics card" scene, a grocery shelf matching a
"discounts" scene under metadata-only scoring) at the unit level; see
test_asset_acquisition.py for the same scenarios exercised end to end
through acquire_assets().
"""

from __future__ import annotations

import json

import pytest

from scripts.production.models import Scene
from scripts.production.providers.llm import LlmProvider, VisionEvaluationError
from scripts.production.providers.visual import AssetResult
from scripts.production.visual_relevance import ScoredCandidate
from scripts.production.vision_validation import (
    VISION_RELEVANCE_THRESHOLD,
    VisionEvaluation,
    build_vision_prompt,
    evaluate_candidate,
    fetch_thumbnail_bytes,
    parse_vision_response,
    select_vision_validated_candidate,
)


class _FakeLlmProvider(LlmProvider):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate_script(self, prompt: str) -> str:
        raise NotImplementedError

    def evaluate_visual_candidate(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        self.calls.append({"image_bytes": image_bytes, "mime_type": mime_type, "prompt": prompt})
        if not self._responses:
            raise VisionEvaluationError("no more fake responses")
        return self._responses.pop(0)


def _asset(page_url: str = "https://pexels.com/video/x-1", thumbnail_url: str = "https://cdn.example.com/thumb.jpg") -> AssetResult:
    return AssetResult(
        provider="pexels",
        page_url=page_url,
        download_url="https://cdn.example.com/file.mp4",
        width=1080,
        height=1920,
        is_video=True,
        duration_seconds=10.0,
        attribution="Video by Someone",
        thumbnail_url=thumbnail_url,
    )


def _scored(asset: AssetResult, score: float = 0.5) -> ScoredCandidate:
    return ScoredCandidate(asset=asset, query="graphics card gpu close up", score=score, shot_type="hardware_detail", reason="metadata match")


def _scene() -> Scene:
    return Scene(index=0, narration_line="This graphics card delivers a huge performance boost")


class _FakeThumbnailResponse:
    content = b"fake-thumbnail-bytes"

    def raise_for_status(self) -> None:
        pass


def _patch_fetch(monkeypatch) -> None:
    """select_vision_validated_candidate downloads a real thumbnail via
    plain requests.get before calling the (fake) LlmProvider -- this must
    be mocked too, or every candidate is rejected by a real network error
    before the fake provider is ever consulted."""
    monkeypatch.setattr("scripts.production.vision_validation.requests.get", lambda url, timeout=None: _FakeThumbnailResponse())


def test_build_vision_prompt_includes_content_domain_anchor():
    prompt = build_vision_prompt(_scene(), "PC graphics card GPU close up")
    assert "PC gaming, gaming hardware, computer components" in prompt


def test_build_vision_prompt_includes_scene_and_query():
    prompt = build_vision_prompt(_scene(), "PC graphics card GPU close up")
    assert "This graphics card delivers a huge performance boost" in prompt
    assert "PC graphics card GPU close up" in prompt


def test_build_vision_prompt_names_hard_rejection_categories():
    prompt = build_vision_prompt(_scene(), "q")
    assert "grocery" in prompt
    assert "paper greeting" in prompt or "bank/credit card" in prompt


def test_parse_vision_response_valid_json():
    raw = json.dumps({"computer_domain": True, "scene_relevance_score": 90, "misleading": False, "reason": "ok"})
    data = parse_vision_response(raw)
    assert data["computer_domain"] is True
    assert data["scene_relevance_score"] == 90


def test_parse_vision_response_strips_markdown_fence():
    raw = "```json\n" + json.dumps({"computer_domain": True, "scene_relevance_score": 90, "misleading": False, "reason": "ok"}) + "\n```"
    data = parse_vision_response(raw)
    assert data["scene_relevance_score"] == 90


def test_parse_vision_response_invalid_json_raises():
    with pytest.raises(VisionEvaluationError):
        parse_vision_response("not json at all")


def test_parse_vision_response_missing_field_raises():
    raw = json.dumps({"computer_domain": True, "misleading": False})  # no scene_relevance_score
    with pytest.raises(VisionEvaluationError):
        parse_vision_response(raw)


def test_vision_evaluation_passes_only_when_in_domain_high_score_and_not_misleading():
    assert VisionEvaluation(computer_domain=True, scene_relevance_score=90, misleading=False, reason="").passed is True
    assert VisionEvaluation(computer_domain=False, scene_relevance_score=90, misleading=False, reason="").passed is False
    assert VisionEvaluation(computer_domain=True, scene_relevance_score=90, misleading=True, reason="").passed is False
    assert VisionEvaluation(computer_domain=True, scene_relevance_score=VISION_RELEVANCE_THRESHOLD - 1, misleading=False, reason="").passed is False
    assert VisionEvaluation(computer_domain=True, scene_relevance_score=VISION_RELEVANCE_THRESHOLD, misleading=False, reason="").passed is True


def test_evaluate_candidate_calls_provider_with_image_bytes_and_mime_type():
    provider = _FakeLlmProvider([json.dumps({"computer_domain": True, "scene_relevance_score": 80, "misleading": False, "reason": "ok"})])

    evaluation = evaluate_candidate(provider, b"\xff\xd8fake-jpeg", "image/jpeg", _scene(), "graphics card gpu")

    assert evaluation.passed is True
    assert provider.calls[0]["image_bytes"] == b"\xff\xd8fake-jpeg"
    assert provider.calls[0]["mime_type"] == "image/jpeg"


def test_select_vision_validated_candidate_returns_first_approved(monkeypatch):
    _patch_fetch(monkeypatch)
    approved_asset = _asset("https://pexels.com/video/gpu-in-case-1")
    provider = _FakeLlmProvider([json.dumps({"computer_domain": True, "scene_relevance_score": 95, "misleading": False, "reason": "a real GPU"})])

    result = select_vision_validated_candidate(provider, [_scored(approved_asset)], _scene())

    assert result is not None
    assert result.asset is approved_asset
    assert "vision approved" in result.reason


def test_select_vision_validated_candidate_rejects_paper_card_for_gpu_scene(monkeypatch):
    """The exact real failure this fixes: a paper greeting card matched a
    "graphics card" scene under metadata-only scoring."""
    _patch_fetch(monkeypatch)
    paper_card_asset = _asset("https://pexels.com/photo/birthday-greeting-card-1")
    provider = _FakeLlmProvider(
        [json.dumps({"computer_domain": False, "scene_relevance_score": 5, "misleading": True, "reason": "shows a paper greeting card, not a GPU"})]
    )

    result = select_vision_validated_candidate(provider, [_scored(paper_card_asset)], _scene())

    assert result is None


def test_select_vision_validated_candidate_rejects_grocery_shelf_for_discount_scene(monkeypatch):
    _patch_fetch(monkeypatch)
    grocery_asset = _asset("https://pexels.com/photo/grocery-store-shelf-jam-1")
    discount_scene = Scene(index=0, narration_line="Here are today's best PC hardware discounts")
    provider = _FakeLlmProvider(
        [json.dumps({"computer_domain": False, "scene_relevance_score": 8, "misleading": False, "reason": "shows a grocery store shelf, not hardware"})]
    )

    result = select_vision_validated_candidate(provider, [_scored(grocery_asset)], discount_scene)

    assert result is None


def test_select_vision_validated_candidate_falls_through_to_the_next_candidate_on_rejection(monkeypatch):
    _patch_fetch(monkeypatch)
    rejected = _asset("https://pexels.com/photo/rejected-1")
    approved = _asset("https://pexels.com/video/approved-1")
    provider = _FakeLlmProvider(
        [
            json.dumps({"computer_domain": False, "scene_relevance_score": 5, "misleading": False, "reason": "no"}),
            json.dumps({"computer_domain": True, "scene_relevance_score": 90, "misleading": False, "reason": "yes"}),
        ]
    )

    result = select_vision_validated_candidate(provider, [_scored(rejected), _scored(approved)], _scene())

    assert result is not None
    assert result.asset is approved
    assert len(provider.calls) == 2


def test_select_vision_validated_candidate_picks_the_highest_scoring_approved_candidate(monkeypatch):
    """Do not stop at the first candidate that clears the threshold -- among
    every candidate that PASSES, the highest scene_relevance_score wins,
    even if a lower-scoring (but still passing) candidate was evaluated
    first. candidate A=72, candidate B=94, candidate C=rejected -> B wins."""
    _patch_fetch(monkeypatch)
    candidate_a = _asset("https://pexels.com/video/candidate-a-72")
    candidate_b = _asset("https://pexels.com/video/candidate-b-94")
    candidate_c = _asset("https://pexels.com/video/candidate-c-rejected")
    provider = _FakeLlmProvider(
        [
            json.dumps({"computer_domain": True, "scene_relevance_score": 72, "misleading": False, "reason": "a weaker but valid match"}),
            json.dumps({"computer_domain": True, "scene_relevance_score": 94, "misleading": False, "reason": "the strongest match"}),
            json.dumps({"computer_domain": False, "scene_relevance_score": 10, "misleading": False, "reason": "not PC/gaming related"}),
        ]
    )

    result = select_vision_validated_candidate(
        provider, [_scored(candidate_a), _scored(candidate_b), _scored(candidate_c)], _scene()
    )

    assert result is not None
    assert result.asset is candidate_b
    assert len(provider.calls) == 3  # every shortlisted candidate was evaluated, not just until the first pass


def test_select_vision_validated_candidate_returns_none_when_everything_is_rejected(monkeypatch):
    _patch_fetch(monkeypatch)
    provider = _FakeLlmProvider(
        [json.dumps({"computer_domain": False, "scene_relevance_score": 5, "misleading": False, "reason": "no"})]
    )

    result = select_vision_validated_candidate(provider, [_scored(_asset())], _scene())

    assert result is None


def test_select_vision_validated_candidate_skips_candidates_without_a_thumbnail():
    no_thumbnail_asset = _asset(thumbnail_url=None)
    provider = _FakeLlmProvider([])  # must never be called

    result = select_vision_validated_candidate(provider, [_scored(no_thumbnail_asset)], _scene())

    assert result is None
    assert provider.calls == []


def test_select_vision_validated_candidate_treats_a_provider_error_as_a_rejection(monkeypatch):
    """A Vision call failure (network, malformed JSON, ...) must reject the
    candidate rather than crash the whole scene's asset search."""
    _patch_fetch(monkeypatch)
    provider = _FakeLlmProvider([])  # raises VisionEvaluationError on the only call

    result = select_vision_validated_candidate(provider, [_scored(_asset())], _scene())

    assert result is None


def test_fetch_thumbnail_bytes_never_downloads_the_full_asset(monkeypatch):
    captured_urls = []

    class _FakeResponse:
        content = b"small-thumbnail-bytes"

        def raise_for_status(self):
            pass

    def fake_get(url, timeout=None):
        captured_urls.append(url)
        return _FakeResponse()

    monkeypatch.setattr("scripts.production.vision_validation.requests.get", fake_get)

    content, mime_type = fetch_thumbnail_bytes("https://cdn.example.com/preview.jpg")

    assert content == b"small-thumbnail-bytes"
    assert mime_type == "image/jpeg"
    assert captured_urls == ["https://cdn.example.com/preview.jpg"]
