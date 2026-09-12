import json
from unittest.mock import Mock

import pytest

from scripts.production.content_brief import ContentBrief, generate_content_brief
from scripts.production.providers.llm import (
    LlmProvider, ScriptGenerationError, build_content_brief_prompt,
    parse_content_brief_response, to_content_brief,
)
from tests.production.test_script_agent import _scored_candidate


def brief_data():
    return dict(target_audience="PC gamers", viewer_pain="Noisy PC", topic_angle="Check cooling",
                hook_candidates=[" Hear your PC? ", "Check your fans."],
                selected_hook=" Hear your PC? ", hook_rationale="A recognizable problem")


def test_prompt_contains_context_and_hook_instructions():
    prompt = build_content_brief_prompt(
        title="PC cooling", content_pillar="optimization", content_role="growth",
        monetization_path=None, summary="Quiet fans", hardware_tier=None, game_title=None,
        scoring_reasoning=["Useful"], evidence=[{"title": "Fan guide"}],
    )
    for text in ["PC cooling", "optimization", "2-3 hook candidates", "first 2 seconds",
                 "NEVER fabricate", "Fan guide", "Quiet fans"]:
        assert text in prompt


@pytest.mark.parametrize("fenced", [False, True])
def test_parse_valid_brief(fenced):
    raw = json.dumps(brief_data())
    if fenced:
        raw = f"```json\n{raw}\n```"
    assert parse_content_brief_response(raw) == brief_data()


@pytest.mark.parametrize("raw", ["not JSON", "{}", "[]", "null", '{"target_audience": "PC"}'])
def test_parse_rejects_malformed_or_missing_fields(raw):
    with pytest.raises(ScriptGenerationError):
        parse_content_brief_response(raw)


def test_normalizes_and_round_trips_brief():
    data = brief_data()
    data["hook_candidates"] += ["Third hook", "Fourth hook"]
    brief = to_content_brief(data, title="PC cooling")
    assert brief.hook_candidates == ["Hear your PC?", "Check your fans.", "Third hook"]
    assert brief.selected_hook == "Hear your PC?"
    assert ContentBrief.from_dict(brief.to_dict()) == brief


@pytest.mark.parametrize("update", [
    {"hook_candidates": ["One"]}, {"hook_candidates": "not an array"},
    {"hook_candidates": ["One", 2]}, {"hook_candidates": ["One", " "]},
    {"selected_hook": "Unlisted"}, {"target_audience": None}, {"viewer_pain": ""},
])
def test_rejects_invalid_brief(update):
    with pytest.raises(ScriptGenerationError):
        to_content_brief(brief_data() | update, title="PC cooling")


def test_selected_hook_equality_is_case_insensitive():
    brief = to_content_brief(brief_data() | {"selected_hook": "HEAR YOUR PC?"}, title="PC")
    assert brief.selected_hook == "HEAR YOUR PC?"


def test_generate_calls_new_provider_method_without_fabrication_guard():
    data = brief_data() | {"hook_candidates": ["240 fps?", "Check your fans."], "selected_hook": "240 fps?"}
    provider = Mock(spec=LlmProvider)
    provider.generate_content_brief.return_value = json.dumps(data)
    brief = generate_content_brief(provider, _scored_candidate())
    assert isinstance(brief, ContentBrief)
    assert brief.selected_hook == "240 fps?"
    provider.generate_content_brief.assert_called_once()
    provider.generate_script.assert_not_called()
