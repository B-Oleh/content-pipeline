"""scene_intent.py tests -- deterministic (no Gemini call) scene-intent
classification and query expansion.

See asset_acquisition.py for how this feeds the search step: a bigger,
intent-appropriate pool of queries per scene increases the chance a real,
honest visual is found before ever falling back to an info card.
"""

from __future__ import annotations

from scripts.production.models import Scene
from scripts.production.scene_intent import (
    ABSTRACT_TECH,
    HARDWARE_DETAIL,
    MAX_QUERIES_PER_SCENE,
    MONITOR_UI,
    OPTIMIZATION_SETTINGS,
    PC_SETUP,
    PERSON_USE_CASE,
    SHOPPING_BUYING,
    classify_scene_intent,
    effective_queries_for_scene,
    expand_queries_for_intent,
)


def _scene(narration: str, queries: list[str] | None = None, on_screen_text: str | None = None) -> Scene:
    return Scene(index=0, narration_line=narration, on_screen_text=on_screen_text, visual_search_queries=queries or [])


def test_classifies_hardware_detail_intent():
    scene = _scene("This GPU has a lot of VRAM and a big cooler", ["gpu cooler close up"])
    assert classify_scene_intent(scene) == HARDWARE_DETAIL


def test_classifies_pc_setup_intent():
    scene = _scene("Check out this clean gaming desk setup", ["gaming desk workspace"])
    assert classify_scene_intent(scene) == PC_SETUP


def test_classifies_monitor_ui_intent():
    scene = _scene("Adjust the resolution in the display menu", ["screen display settings"])
    assert classify_scene_intent(scene) == MONITOR_UI


def test_classifies_person_use_case_intent():
    scene = _scene("Watch this gamer unboxing their new keyboard", ["person unboxing gaming gear"])
    assert classify_scene_intent(scene) == PERSON_USE_CASE


def test_classifies_shopping_buying_intent():
    scene = _scene("Should you buy this GPU at the current price", ["comparing gpu deals"])
    assert classify_scene_intent(scene) == SHOPPING_BUYING


def test_classifies_optimization_settings_intent():
    scene = _scene("Tweak these settings to boost your FPS", ["optimize game settings"])
    assert classify_scene_intent(scene) == OPTIMIZATION_SETTINGS


def test_falls_back_to_abstract_tech_when_nothing_matches():
    scene = _scene("Technology keeps changing every year", [])
    assert classify_scene_intent(scene) == ABSTRACT_TECH


def test_shopping_intent_wins_over_hardware_detail_when_both_present():
    """Ordering matters: a scene that is clearly about buying should not be
    miscategorized as hardware_detail just because it also names a
    component (see _INTENT_KEYWORDS' ordering comment)."""
    scene = _scene("Compare the price before you buy this graphics card", ["buying a gpu comparison"])
    assert classify_scene_intent(scene) == SHOPPING_BUYING


def test_on_screen_text_contributes_to_classification():
    scene = _scene("Some narration with no clear keywords", on_screen_text="optimize your fps settings")
    assert classify_scene_intent(scene) == OPTIMIZATION_SETTINGS


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------


def test_expand_queries_keeps_existing_queries_first():
    existing = ["a specific existing query"]
    expanded = expand_queries_for_intent(HARDWARE_DETAIL, existing)
    assert expanded[0] == "a specific existing query"


def test_expand_queries_adds_new_generic_phrases():
    expanded = expand_queries_for_intent(HARDWARE_DETAIL, ["existing query"])
    assert len(expanded) > 1


def test_expand_queries_deduplicates_case_insensitively():
    expanded = expand_queries_for_intent(HARDWARE_DETAIL, ["Computer Hardware Close Up"])
    lowered = [q.lower() for q in expanded]
    assert lowered.count("computer hardware close up") == 1


def test_expand_queries_is_capped_at_max_queries_per_scene():
    many_existing = [f"existing query {i}" for i in range(20)]
    expanded = expand_queries_for_intent(HARDWARE_DETAIL, many_existing)
    assert len(expanded) <= MAX_QUERIES_PER_SCENE


def test_query_templates_vary_by_intent():
    """Part 7's explicit requirement: visual query generation varies by
    scene intent -- different intents must not expand to the same generic
    phrases."""
    hardware_expansion = set(expand_queries_for_intent(HARDWARE_DETAIL, []))
    shopping_expansion = set(expand_queries_for_intent(SHOPPING_BUYING, []))
    setup_expansion = set(expand_queries_for_intent(PC_SETUP, []))

    assert hardware_expansion != shopping_expansion
    assert hardware_expansion != setup_expansion
    assert shopping_expansion != setup_expansion


def test_effective_queries_for_scene_combines_scene_queries_with_intent_expansion():
    scene = _scene("Should you buy this GPU at the current price", ["comparing gpu deals"])
    queries = effective_queries_for_scene(scene)
    assert "comparing gpu deals" in queries
    assert len(queries) > 1


def test_effective_queries_for_scene_defaults_to_narration_when_no_queries_given():
    scene = _scene("A gaming desk setup with RGB lighting", [])
    queries = effective_queries_for_scene(scene)
    assert queries[0] == scene.narration_line
