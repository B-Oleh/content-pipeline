"""LLM provider boundary for Script Agent.

Thin and replaceable per CLAUDE.md "Provider abstraction": script_agent.py
only ever calls the LlmProvider interface below, never the Gemini SDK
directly, so a future free-tier alternative can be swapped in by adding one
class here.
"""

from __future__ import annotations

import json
import re
from typing import Any

from scripts.production.models import Scene, VideoScript
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

# The most concretely fabricable "hard numbers" this niche's rules forbid
# inventing (see CLAUDE.md "Fact checking" and "Research and opportunity
# scoring rules"). This is a safety net on top of the prompt instructions
# below, not a substitute for them -- it deliberately does not try to catch
# every possible fabrication (e.g. a bare year), since a niche where topics
# themselves legitimately mention years/models would make that guard too
# noisy to trust. FPS/price/percentage-performance numbers are the sharpest,
# least ambiguous signal of an invented benchmark or price.
_FABRICATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d{1,4}\s?fps\b", re.IGNORECASE), "a specific FPS number"),
    (re.compile(r"[$€£]\s?\d+(\.\d{1,2})?"), "a specific price"),
    (
        re.compile(r"\b\d{1,3}\s?%\s*(faster|slower|better|worse|improvement|increase|decrease|boost)\b", re.IGNORECASE),
        "a specific performance percentage",
    ),
]


class ScriptGenerationError(RuntimeError):
    """Raised when Script Agent cannot produce a script that respects the
    "never invent FPS/prices/specs/dates/popularity/performance" rule, or
    when the LLM provider itself is unusable."""


def find_fabrication_risks(text: str) -> list[str]:
    """Return a list of human-readable violations found in text, if any."""
    violations = []
    for pattern, description in _FABRICATION_PATTERNS:
        if pattern.search(text):
            violations.append(f"{description} ({pattern.pattern!r} matched)")
    return violations


class LlmProvider:
    """Interface every LLM provider must implement."""

    def generate_script(self, prompt: str) -> str:
        """Return the raw text response for a fully-built prompt."""
        raise NotImplementedError


class GeminiProvider(LlmProvider):
    """Google Gemini, via the official google-genai SDK."""

    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL) -> None:
        # Imported lazily so importing this module (e.g. for the fabrication
        # guard alone, in tests) never requires the google-genai package to
        # be configured with a real key.
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate_script(self, prompt: str) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                response_mime_type="application/json",
            ),
        )
        if not response.text:
            raise ScriptGenerationError("Gemini returned an empty response")
        return response.text

    def ping(self) -> None:
        """Minimal, cheap connectivity/auth check used by preflight.py.

        Raises on any failure; callers should not try to interpret the
        response content, only whether the call succeeded.
        """
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents="Reply with exactly one word: OK",
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=16),
        )
        if not response.text:
            raise ScriptGenerationError("Gemini preflight ping returned an empty response")


_SCRIPT_JSON_INSTRUCTIONS = """
Respond with ONLY a single JSON object (no markdown fences, no commentary) with this exact shape:

{{
  "title": "short YouTube Shorts title, under 100 characters",
  "description": "1-2 sentence video description",
  "hook": "one short opening sentence that earns attention in the first 2 seconds",
  "scenes": [
    {{
      "narration_line": "1-2 short spoken sentences for this scene",
      "on_screen_text": "very short on-screen caption text, or empty string",
      "visual_search_queries": ["generic stock-footage search phrase", "another one"]
    }}
  ],
  "evidence_references": ["short phrase naming which piece of evidence supports a claim, or general phrase if none needed"]
}}

Rules:
- Produce 4 to 6 scenes.
- Total spoken narration (hook + every scene's narration_line combined) must read aloud in
  approximately 30-45 seconds at a natural pace (roughly 90-115 words total).
- visual_search_queries must be GENERIC, stock-footage-friendly phrases (e.g. "gaming pc setup rgb",
  "person typing keyboard closeup", "computer hardware close up") -- never a specific game title or
  exact product name, since stock video libraries will not have that exact footage. Do not write
  narration or on_screen_text that claims the video is showing a specific named product or game if
  only generic footage will illustrate it -- keep such lines honest about being illustrative/generic.
- NEVER state a specific FPS number, benchmark result, price, exact hardware specification, release
  date, popularity/sales statistic, or performance percentage unless it is explicitly present in the
  evidence provided below. If the evidence does not support a specific number, speak in general,
  qualitative terms instead (e.g. "smoother performance" instead of "40% faster", "check current
  pricing" instead of a dollar amount).
- Keep every sentence short and easy to follow when read aloud quickly.
"""


def build_script_prompt(
    *,
    title: str,
    content_pillar: str,
    content_role: str,
    monetization_path: str | None,
    summary: str | None,
    hardware_tier: str | None,
    game_title: str | None,
    scoring_reasoning: list[str],
    evidence: list[dict[str, Any]],
) -> str:
    """Build the full prompt text for GeminiProvider.generate_script().

    Kept as a pure function (no SDK dependency) so it is directly unit
    testable without a real API key.
    """
    evidence_lines = []
    for item in evidence:
        source = item.get("source_name", "unknown source")
        item_title = item.get("title") or ""
        item_summary = item.get("summary") or ""
        url = item.get("source_url") or ""
        evidence_lines.append(f"- [{source}] {item_title} -- {item_summary} ({url})".strip())
    evidence_block = "\n".join(evidence_lines) if evidence_lines else "(no external evidence items -- rely only on the topic itself, stay general)"

    context_lines = [
        f"Topic: {title}",
        f"Content pillar: {content_pillar}",
        f"Content role: {content_role}",
    ]
    if monetization_path:
        context_lines.append(f"Monetization angle: {monetization_path}")
    if summary:
        context_lines.append(f"Research summary: {summary}")
    if hardware_tier:
        context_lines.append(f"Hardware tier: {hardware_tier}")
    if game_title:
        context_lines.append(f"Game mentioned: {game_title}")
    if scoring_reasoning:
        context_lines.append("Why this topic was selected: " + "; ".join(scoring_reasoning))

    return (
        "You are writing a factual, honest short-form (vertical, YouTube Shorts style) video script "
        "for a gaming hardware/technology channel.\n\n"
        + "\n".join(context_lines)
        + "\n\nEvidence available for this topic:\n"
        + evidence_block
        + "\n\n"
        + _SCRIPT_JSON_INSTRUCTIONS.format()
    )


def parse_script_response(raw_text: str, *, retry_note: str = "") -> dict[str, Any]:
    """Parse and lightly validate the JSON shape Gemini was asked for.

    Strips a markdown code fence if the model added one anyway. Raises
    ScriptGenerationError on invalid JSON or a missing required field --
    fail loudly rather than silently producing a broken script (see
    CLAUDE.md "Script organization").
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScriptGenerationError(f"Gemini did not return valid JSON{retry_note}: {exc}") from exc

    required = ("title", "description", "hook", "scenes")
    missing = [field for field in required if field not in data]
    if missing:
        raise ScriptGenerationError(f"Gemini response is missing required field(s): {missing}")
    if not isinstance(data["scenes"], list) or not data["scenes"]:
        raise ScriptGenerationError("Gemini response has no scenes")
    return data


def to_video_script(
    data: dict[str, Any],
    *,
    topic: str,
    content_role: str,
    candidate_id: str | None,
    overall_score: float | None,
) -> VideoScript:
    hook = str(data["hook"]).strip()
    scenes = []
    for index, raw_scene in enumerate(data["scenes"]):
        narration_line = str(raw_scene.get("narration_line", "")).strip()
        scenes.append(
            Scene(
                index=index,
                # The hook is spoken over scene 0's visual rather than as a
                # separate scene -- it is still exposed on VideoScript.hook
                # (e.g. for the Telegram caption) unmerged.
                narration_line=f"{hook} {narration_line}".strip() if index == 0 else narration_line,
                on_screen_text=(str(raw_scene["on_screen_text"]).strip() or None)
                if raw_scene.get("on_screen_text")
                else None,
                visual_search_queries=[str(q).strip() for q in raw_scene.get("visual_search_queries", []) if str(q).strip()],
            )
        )
    narration = " ".join([hook] + [str(s.get("narration_line", "")).strip() for s in data["scenes"]]).strip()
    return VideoScript(
        topic=topic,
        content_role=content_role,
        hook=hook,
        narration=narration,
        scenes=scenes,
        title=str(data["title"]).strip(),
        description=str(data["description"]).strip(),
        evidence_references=[str(r) for r in data.get("evidence_references", [])],
        candidate_id=candidate_id,
        overall_score=overall_score,
    )
