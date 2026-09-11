"""LLM provider boundary for Script Agent.

Thin and replaceable per CLAUDE.md "Provider abstraction": script_agent.py
only ever calls the LlmProvider interface below, never the Gemini SDK
directly, so a future free-tier alternative can be swapped in by adding one
class here.
"""

from __future__ import annotations

import base64
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
    "never invent FPS/prices/specs/dates/popularity/performance" rule, when
    a structured-script Gemini call is unusable, or when its response is
    malformed/empty."""


class GeminiPingError(RuntimeError):
    """Raised when the minimal preflight connectivity check (GeminiProvider.ping)
    fails -- kept distinct from ScriptGenerationError so a preflight
    connectivity/auth problem is never reported as if it were a structured
    script generation problem (see preflight.py)."""


class VisionEvaluationError(RuntimeError):
    """Raised when a Gemini Vision call for candidate-asset relevance fails
    or returns no usable output (see vision_validation.py, which catches
    this per-candidate so one failed evaluation cannot abort a whole
    scene's asset search)."""


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

    def evaluate_visual_candidate(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        """Return the raw JSON text response for a vision-based relevance
        evaluation of one candidate image (see vision_validation.py, which
        owns the prompt content and response parsing -- this method is only
        the thin vendor-call boundary, per CLAUDE.md "Provider abstraction").
        """
        raise NotImplementedError


# JSON schema for structured script output via the Interactions API's
# response_format (see GeminiProvider.generate_script). This is enforced
# server-side on top of -- not instead of -- _SCRIPT_JSON_INSTRUCTIONS
# below, which also carries semantic rules (scene count, word count,
# generic visual queries, no fabricated claims) a JSON schema cannot
# express.
SCRIPT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hook": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration_line": {"type": "string"},
                    "on_screen_text": {"type": "string"},
                    "visual_search_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 5,
                    },
                },
                "required": ["narration_line"],
            },
        },
        "evidence_references": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "hook", "scenes"],
}

# JSON schema for the Gemini Vision candidate-relevance call (see
# vision_validation.py, which owns the actual prompt text and hard
# rejection rules -- this schema only shapes the response).
VISION_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "computer_domain": {"type": "boolean"},
        "scene_relevance_score": {"type": "integer"},
        "misleading": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["computer_domain", "scene_relevance_score", "misleading", "reason"],
}

_PING_PROMPT = "Reply exactly with OK"

# Statuses the Interactions API can return without ever raising a Python
# exception (see google.genai.interactions.InteractionStatus) -- anything
# other than "completed" means there is no usable output_text, and the
# reason should come from the interaction's own `errors`, not a guess.
_INTERACTION_FAILURE_HINT = "did not complete successfully"


def _describe_incomplete_interaction(interaction: Any) -> str:
    """A safe, useful description of why an Interaction has no output_text.

    Uses only the interaction's own status and error messages (both
    provider-supplied, never request/credential data) -- see
    preflight.py's "never print secret values" rule.
    """
    status = getattr(interaction, "status", "unknown")
    errors = getattr(interaction, "errors", None) or []
    messages = [error.message for error in errors if getattr(error, "message", None)]
    if messages:
        return f"Gemini interaction {_INTERACTION_FAILURE_HINT} (status={status}): {'; '.join(messages)}"
    return f"Gemini interaction {_INTERACTION_FAILURE_HINT} (status={status}) with no output text"


class GeminiProvider(LlmProvider):
    """Google Gemini, via the official google-genai SDK's Interactions API
    (client.interactions.create) -- not the older Models.generate_content,
    whose automatic function calling (AFC) machinery was firing even for a
    plain text-only prompt with no tools configured (see
    docs/PRODUCTION_PIPELINE.md "Gemini preflight" for the incident this
    fixed). Interactions has no client-side AFC concept at all: tools are
    explicit server-side declarations (google.genai.interactions.Tool) that
    this provider never passes.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL, client: Any = None) -> None:
        if client is None:
            # Imported lazily so importing this module (e.g. for the
            # fabrication guard alone, in tests) never requires the
            # google-genai package to be configured with a real key.
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self._model = model

    def generate_script(self, prompt: str) -> str:
        interaction = self._client.interactions.create(
            model=self._model,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": SCRIPT_JSON_SCHEMA,
            },
        )
        text = getattr(interaction, "output_text", None)
        if not text:
            raise ScriptGenerationError(_describe_incomplete_interaction(interaction))
        return text

    def evaluate_visual_candidate(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        """One multimodal (text + image) Interactions call -- confirmed
        against the real, installed google-genai SDK's own request model
        that `input` must be a list of `{"type": "user_input", "content":
        [...]}` steps (a bare list of content dicts is silently
        misinterpreted as unrecognized "steps", not content -- see
        tests/production/test_gemini_provider.py for the same real-SDK
        validation approach used for generate_script's response_format).
        """
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        interaction = self._client.interactions.create(
            model=self._model,
            input=[
                {
                    "type": "user_input",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "data": image_b64, "mime_type": mime_type},
                    ],
                }
            ],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": VISION_EVALUATION_SCHEMA,
            },
        )
        text = getattr(interaction, "output_text", None)
        if not text:
            raise VisionEvaluationError(_describe_incomplete_interaction(interaction))
        return text

    def ping(self) -> None:
        """Minimal, tools-free, schema-free text-only connectivity check
        used by preflight.py.

        Deliberately passes nothing beyond model + input: no tools, no
        automatic function calling, no response_format/JSON schema -- so a
        plain connectivity/auth failure can never be confused with (or
        masked by) a structured-output or tool-calling problem.
        """
        interaction = self._client.interactions.create(model=self._model, input=_PING_PROMPT)
        text = getattr(interaction, "output_text", None)
        if not text:
            raise GeminiPingError(_describe_incomplete_interaction(interaction))


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
      "visual_search_queries": ["3 to 5 concrete, specific stock-footage search phrases for THIS scene"]
    }}
  ],
  "evidence_references": ["short phrase naming which piece of evidence supports a claim, or general phrase if none needed"]
}}

Rules:
- Produce 4 to 6 scenes.
- Total spoken narration (hook + every scene's narration_line combined) must read aloud in
  approximately 30-45 seconds at a natural pace (roughly 90-115 words total).
- Each scene's visual_search_queries must contain 3 to 5 CONCRETE, specific phrases describing what
  should visibly be on screen for THAT scene -- specific enough that a stock-video search would return
  a genuinely matching clip. Bad (too vague): "gaming", "computer". Better: "gaming laptop keyboard
  close up", "desktop gpu inside pc case", "pc game performance settings menu", "person comparing
  computer hardware", "high refresh rate gaming monitor". Vary what each query describes across the
  list for one scene (e.g. a close-up, a hardware-detail shot, a monitor/UI shot, a person/use-case
  shot) so the pipeline has real options to choose from, not five near-duplicate phrasings of the same
  shot. Also vary the dominant shot type ACROSS scenes -- avoid every scene being the same kind of shot
  (e.g. all close-ups, or all person-at-desk shots).
- Never write a visual_search_queries phrase that names a specific game title or exact product/model
  name -- stock video libraries will not have that exact footage. Do not write narration or
  on_screen_text that claims the video is showing a specific named product or game if only generic
  footage will illustrate it -- keep such lines honest about being illustrative/generic.
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
