"""Audience, angle, and opening-hook design for one research candidate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from scripts.production.providers import llm
from scripts.research.models import ScoredCandidate


@dataclass
class ContentBrief:
    target_audience: str
    viewer_pain: str
    topic_angle: str
    hook_candidates: list[str]
    selected_hook: str
    hook_rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentBrief:
        return llm.to_content_brief(data, title="saved brief")


def generate_content_brief(provider: llm.LlmProvider, scored_candidate: ScoredCandidate) -> ContentBrief:
    candidate = scored_candidate.candidate
    prompt = llm.build_content_brief_prompt(
        title=candidate.title,
        content_pillar=candidate.content_pillar.value,
        content_role=candidate.content_role.value,
        monetization_path=candidate.monetization_path,
        summary=candidate.summary,
        hardware_tier=candidate.hardware_tier.value if candidate.hardware_tier else None,
        game_title=candidate.game_title,
        scoring_reasoning=scored_candidate.reasoning,
        evidence=(candidate.raw_metadata or {}).get("evidence", []),
    )
    data = llm.parse_content_brief_response(provider.generate_content_brief(prompt))
    return llm.to_content_brief(data, title=candidate.title)
