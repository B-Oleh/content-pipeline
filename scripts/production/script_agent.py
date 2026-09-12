"""Script Agent: turns one ranked ResearchCandidate into a VideoScript.

See CLAUDE.md pipeline stage 4. Calls the LlmProvider interface
(providers/llm.py) -- never a vendor SDK directly -- and enforces the
"never invent FPS/prices/specs/dates/popularity/performance" rule with an
automated regex safety net on top of the prompt instructions, retrying once
with a stricter prompt before failing loudly (see CLAUDE.md "Fact checking":
"When confidence is low, prefer flagging or dropping the claim over
publishing a guess").
"""

from __future__ import annotations

from scripts.production.content_brief import ContentBrief
from scripts.production.models import VideoScript
from scripts.production.providers.llm import (
    LlmProvider,
    ScriptGenerationError,
    build_script_prompt,
    find_fabrication_risks,
    parse_script_response,
    to_video_script,
)
from scripts.research.models import ScoredCandidate
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)


def _fabrication_violations(script: VideoScript) -> list[str]:
    violations = []
    for text in [script.hook] + [scene.narration_line for scene in script.scenes] + [
        scene.on_screen_text for scene in script.scenes if scene.on_screen_text
    ]:
        violations.extend(find_fabrication_risks(text))
    return violations


def generate_script(
    provider: LlmProvider, scored_candidate: ScoredCandidate, content_brief: ContentBrief | None = None,
) -> VideoScript:
    """Generate and validate a VideoScript for one ranked candidate.

    Raises ScriptGenerationError if the provider is unusable, the response
    is malformed, or a fabricated-sounding claim survives one corrective
    retry -- callers must not fall back to sending an unvalidated script.
    """
    candidate = scored_candidate.candidate
    evidence = (candidate.raw_metadata or {}).get("evidence", [])

    prompt = build_script_prompt(
        title=candidate.title,
        content_pillar=candidate.content_pillar.value,
        content_role=candidate.content_role.value,
        monetization_path=candidate.monetization_path,
        summary=candidate.summary,
        hardware_tier=candidate.hardware_tier.value if candidate.hardware_tier else None,
        game_title=candidate.game_title,
        scoring_reasoning=scored_candidate.reasoning,
        evidence=evidence,
        content_brief=content_brief,
    )

    raw_text = provider.generate_script(prompt)
    data = parse_script_response(raw_text)
    script = to_video_script(
        data,
        topic=candidate.title,
        content_role=candidate.content_role.value,
        candidate_id=candidate.candidate_id,
        overall_score=scored_candidate.overall_score,
    )

    violations = _fabrication_violations(script)
    if violations:
        logger.warning("Script for %r contains likely fabricated claim(s): %s -- retrying once", candidate.title, violations)
        retry_prompt = (
            prompt
            + "\n\nYour previous attempt included specific-sounding claim(s) that are not "
            "supported by the evidence above: "
            + "; ".join(violations)
            + ". Rewrite the script so no scene states a specific FPS number, price, exact "
            "specification, release date, popularity statistic, or performance percentage unless "
            "it is explicitly present in the evidence. Prefer a safer, more general claim instead."
        )
        raw_text = provider.generate_script(retry_prompt)
        data = parse_script_response(raw_text, retry_note=" (on retry)")
        script = to_video_script(
            data,
            topic=candidate.title,
            content_role=candidate.content_role.value,
            candidate_id=candidate.candidate_id,
            overall_score=scored_candidate.overall_score,
        )
        violations = _fabrication_violations(script)
        if violations:
            raise ScriptGenerationError(
                f"Script for {candidate.title!r} still contains likely fabricated claim(s) after one "
                f"corrective retry: {violations}. Refusing to proceed with an unverified claim -- "
                "select a different topic or provide stronger evidence instead of retrying again."
            )

    logger.info("Generated script for %r: %d scene(s), %d word narration", candidate.title, len(script.scenes), len(script.narration.split()))
    return script
