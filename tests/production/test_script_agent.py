import json

import pytest

from scripts.production.providers.llm import LlmProvider, ScriptGenerationError
from scripts.production.script_agent import generate_script
from scripts.research.models import ContentPillar, ContentRole, ResearchCandidate, ScoreBreakdown, ScoredCandidate


def _score_kwargs():
    return {
        "audience_interest": 5.0, "commercial_intent": 5.0, "affiliate_potential": 5.0,
        "competition": 5.0, "novelty": 5.0, "visual_potential": 5.0, "retention_potential": 5.0,
        "production_difficulty": 5.0, "confidence": 5.0, "freshness": 5.0, "evergreen_value": 5.0,
    }


def _scored_candidate(**overrides) -> ScoredCandidate:
    kwargs = {
        "candidate_id": "c1",
        "title": "Best budget GPUs",
        "content_pillar": ContentPillar.BUYING_ADVICE,
        "content_role": ContentRole.HYBRID,
        "source_name": "fixture_test",
        "monetization_path": "GPU affiliate links",
    }
    kwargs.update(overrides)
    candidate = ResearchCandidate(**kwargs)
    return ScoredCandidate(candidate=candidate, scores=ScoreBreakdown(**_score_kwargs()), overall_score=7.0, rank=1, reasoning=["good topic"])


class _FakeLlmProvider(LlmProvider):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate_script(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


def _clean_response() -> str:
    return json.dumps(
        {
            "title": "Best Budget GPUs",
            "description": "A quick rundown.",
            "hook": "Want more FPS without breaking the bank?",
            "scenes": [
                {"narration_line": "Here are solid budget picks.", "on_screen_text": "Budget GPUs", "visual_search_queries": ["computer graphics card"]},
                {"narration_line": "Check current pricing before buying.", "on_screen_text": "", "visual_search_queries": ["gaming pc setup"]},
            ],
            "evidence_references": ["fixture_test"],
        }
    )


def test_generate_script_returns_clean_script():
    provider = _FakeLlmProvider([_clean_response()])
    script = generate_script(provider, _scored_candidate())

    assert script.title == "Best Budget GPUs"
    assert len(script.scenes) == 2
    assert len(provider.prompts) == 1


def _fabricated_response() -> str:
    return json.dumps(
        {
            "title": "Best Budget GPUs",
            "description": "A quick rundown.",
            "hook": "This card hits 240 fps easily!",
            "scenes": [
                {"narration_line": "It costs $199.", "on_screen_text": "", "visual_search_queries": ["gpu"]},
            ],
            "evidence_references": [],
        }
    )


def test_generate_script_retries_once_on_fabrication_then_succeeds():
    provider = _FakeLlmProvider([_fabricated_response(), _clean_response()])
    script = generate_script(provider, _scored_candidate())

    assert len(provider.prompts) == 2
    assert "specific-sounding claim" in provider.prompts[1]
    assert script.title == "Best Budget GPUs"


def test_generate_script_fails_loudly_if_fabrication_survives_retry():
    provider = _FakeLlmProvider([_fabricated_response(), _fabricated_response()])
    with pytest.raises(ScriptGenerationError):
        generate_script(provider, _scored_candidate())
    assert len(provider.prompts) == 2
