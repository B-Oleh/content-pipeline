import json

import pytest

from scripts.production.providers.llm import (
    ScriptGenerationError,
    build_script_prompt,
    find_fabrication_risks,
    parse_script_response,
    to_video_script,
)


def test_find_fabrication_risks_detects_fps():
    assert find_fabrication_risks("This runs at 144 fps easily") != []


def test_find_fabrication_risks_detects_price():
    assert find_fabrication_risks("You can grab this for $299") != []


def test_find_fabrication_risks_detects_performance_percentage():
    assert find_fabrication_risks("It is 30% faster than the last generation") != []


def test_find_fabrication_risks_clean_text_has_no_violations():
    assert find_fabrication_risks("This gives noticeably smoother performance in general") == []


def test_build_script_prompt_includes_context_and_evidence():
    prompt = build_script_prompt(
        title="Best budget GPUs",
        content_pillar="buying_advice",
        content_role="hybrid",
        monetization_path="GPU affiliate links",
        summary="Roundup of budget GPUs",
        hardware_tier=None,
        game_title=None,
        scoring_reasoning=["audience_interest scored 8.0/10"],
        evidence=[{"source_name": "pcgamer_rss", "title": "New GPU released", "summary": "details", "source_url": "https://x.com"}],
    )
    assert "Best budget GPUs" in prompt
    assert "GPU affiliate links" in prompt
    assert "pcgamer_rss" in prompt
    assert "New GPU released" in prompt
    assert "NEVER state a specific FPS number" in prompt


def test_build_script_prompt_handles_no_evidence():
    prompt = build_script_prompt(
        title="A topic",
        content_pillar="gaming_technology",
        content_role="growth",
        monetization_path=None,
        summary=None,
        hardware_tier=None,
        game_title=None,
        scoring_reasoning=[],
        evidence=[],
    )
    assert "no external evidence" in prompt


def test_parse_script_response_valid_json():
    raw = json.dumps({"title": "T", "description": "D", "hook": "H", "scenes": [{"narration_line": "line"}]})
    data = parse_script_response(raw)
    assert data["title"] == "T"


def test_parse_script_response_strips_markdown_fence():
    raw = "```json\n" + json.dumps({"title": "T", "description": "D", "hook": "H", "scenes": [{"narration_line": "l"}]}) + "\n```"
    data = parse_script_response(raw)
    assert data["title"] == "T"


def test_parse_script_response_invalid_json_raises():
    with pytest.raises(ScriptGenerationError):
        parse_script_response("not json at all")


def test_parse_script_response_missing_field_raises():
    raw = json.dumps({"title": "T", "description": "D", "hook": "H"})  # no scenes
    with pytest.raises(ScriptGenerationError):
        parse_script_response(raw)


def test_parse_script_response_empty_scenes_raises():
    raw = json.dumps({"title": "T", "description": "D", "hook": "H", "scenes": []})
    with pytest.raises(ScriptGenerationError):
        parse_script_response(raw)


def test_to_video_script_merges_hook_into_scene_zero():
    data = {
        "title": "T",
        "description": "D",
        "hook": "Did you know this?",
        "scenes": [
            {"narration_line": "First fact.", "on_screen_text": "Fact 1", "visual_search_queries": ["pc setup"]},
            {"narration_line": "Second fact.", "on_screen_text": "", "visual_search_queries": []},
        ],
        "evidence_references": ["pcgamer_rss"],
    }
    script = to_video_script(data, topic="T", content_role="growth", candidate_id="c1", overall_score=7.5)

    assert script.hook == "Did you know this?"
    assert script.scenes[0].narration_line == "Did you know this? First fact."
    assert script.scenes[1].narration_line == "Second fact."
    assert script.scenes[1].on_screen_text is None
    assert script.narration.startswith("Did you know this? First fact. Second fact.")
    assert script.candidate_id == "c1"
    assert script.overall_score == 7.5
