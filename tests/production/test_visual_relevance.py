from scripts.production.models import Scene
from scripts.production.providers.visual import AssetResult
from scripts.production.visual_relevance import (
    RELEVANCE_THRESHOLD,
    classify_shot_type,
    extract_keywords,
    score_candidate,
    select_best_candidate,
)


def _asset(page_url: str, width: int = 1080, height: int = 1920, provider: str = "pexels") -> AssetResult:
    return AssetResult(
        provider=provider,
        page_url=page_url,
        download_url="https://cdn.example.com/file.mp4",
        width=width,
        height=height,
        is_video=True,
        duration_seconds=10.0,
        attribution="Video by Someone",
    )


def _scene(narration: str) -> Scene:
    return Scene(index=0, narration_line=narration)


def test_extract_keywords_lowercases_and_strips_stopwords():
    keywords = extract_keywords("This is a Gaming Laptop Keyboard Close-Up")
    assert "gaming" in keywords
    assert "laptop" in keywords
    assert "keyboard" in keywords
    assert "close" in keywords
    assert "this" not in keywords
    assert "is" not in keywords
    assert "a" not in keywords


def test_extract_keywords_drops_short_words():
    assert "pc" not in extract_keywords("the pc is fast")  # len("pc") == 2, dropped


def test_classify_shot_type_close_up():
    assert classify_shot_type("gaming laptop keyboard close up") == "close_up"


def test_classify_shot_type_hardware_detail():
    assert classify_shot_type("desktop gpu inside pc case") == "hardware_detail"


def test_classify_shot_type_monitor_ui():
    assert classify_shot_type("pc game performance settings menu") == "monitor_ui"


def test_classify_shot_type_person_use_case():
    assert classify_shot_type("person typing hands on keyboard") == "person_use_case"


def test_classify_shot_type_defaults_when_no_match():
    assert classify_shot_type("") == "environment_setup"


def test_score_candidate_rewards_specific_keyword_overlap_in_url():
    asset = _asset("https://www.pexels.com/video/desktop-gpu-inside-pc-case-12345")
    scored = score_candidate(asset, _scene("Here is a desktop GPU inside a PC case."), "desktop gpu inside pc case")
    assert scored.score >= RELEVANCE_THRESHOLD
    assert "gpu" in scored.reason or "desktop" in scored.reason


def test_score_candidate_scores_low_for_purely_generic_match():
    """A candidate matched only on RGB/gaming/keyboard/monitor terms should
    NOT score as strongly relevant -- see task's explicit "unless it
    actually supports the scene" requirement."""
    asset = _asset("https://www.pexels.com/video/rgb-gaming-keyboard-monitor-99999")
    scored = score_candidate(
        asset,
        _scene("Should you buy a mechanical keyboard with RGB lighting for gaming"),
        "rgb gaming keyboard monitor",
    )
    assert scored.score < RELEVANCE_THRESHOLD


def test_score_candidate_scores_low_when_url_has_no_overlap_at_all():
    asset = _asset("https://www.pexels.com/video/ocean-waves-beach-sunset-11111")
    scored = score_candidate(asset, _scene("A close look at graphics card thermal performance"), "graphics card cooling")
    assert scored.score < RELEVANCE_THRESHOLD


def test_score_candidate_prefers_portrait_aspect_ratio():
    portrait = _asset("https://example.com/video/x-1", width=1080, height=1920)
    landscape = _asset("https://example.com/video/x-1", width=1920, height=1080)
    scene = _scene("some scene about a computer")
    portrait_score = score_candidate(portrait, scene, "computer").score
    landscape_score = score_candidate(landscape, scene, "computer").score
    assert portrait_score >= landscape_score


def test_score_candidate_penalizes_repeated_shot_type():
    asset = _asset("https://www.pexels.com/video/desktop-gpu-inside-pc-case-1")
    scene = _scene("A desktop GPU inside a PC case")
    without_repeat = score_candidate(asset, scene, "desktop gpu inside pc case", previous_shot_type=None)
    with_repeat = score_candidate(asset, scene, "desktop gpu inside pc case", previous_shot_type="hardware_detail")
    assert with_repeat.score < without_repeat.score
    assert with_repeat.shot_type == "hardware_detail"


def test_score_never_negative():
    asset = _asset("https://example.com/video/totally-unrelated-1")
    scene = _scene("xyz")
    scored = score_candidate(asset, scene, "xyz", previous_shot_type=classify_shot_type("totally-unrelated"))
    assert scored.score >= 0.0


def test_select_best_candidate_picks_highest_score():
    asset_a = _asset("https://example.com/video/desktop-gpu-pc-case-1")
    asset_b = _asset("https://example.com/video/unrelated-beach-1")
    scene = _scene("A desktop GPU inside a PC case")
    scored_a = score_candidate(asset_a, scene, "desktop gpu inside pc case")
    scored_b = score_candidate(asset_b, scene, "desktop gpu inside pc case")

    best = select_best_candidate([scored_b, scored_a])

    assert best is scored_a


def test_select_best_candidate_returns_none_for_empty_list():
    assert select_best_candidate([]) is None
