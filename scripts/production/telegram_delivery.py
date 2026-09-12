"""Telegram delivery: sends the rendered MP4, with real Approve/Regenerate/
Reject inline buttons, and a research-grounded caption.

See CLAUDE.md pipeline stage 10 ("Telegram approval gate") and
docs/PRODUCTION_PIPELINE.md "Telegram approval gate" for how
telegram_approval.py then actually handles a click on one of these
buttons. QA must have passed before this is ever called (see pipeline.py);
this module does not re-check QA.
"""

from __future__ import annotations

from pathlib import Path

from scripts.production.content_brief import ContentBrief
from scripts.production.models import VideoScript
from scripts.production.providers.telegram_client import TelegramClient
from scripts.production.telegram_approval import build_approval_keyboard
from scripts.research.models import SCORE_DIMENSIONS, ScoredCandidate
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)


def build_caption(script: VideoScript, scored_candidate: ScoredCandidate) -> str:
    candidate = scored_candidate.candidate
    reasoning = "; ".join(scored_candidate.reasoning) if scored_candidate.reasoning else "top-ranked by Research Agent"
    lines = [
        f"\U0001f3ae Topic: {script.topic}",
        f"\U0001f4dd Title: {script.title}",
        f"\U0001f3af Content role: {script.content_role}",
        f"⭐ Why selected: {reasoning}",
        f"\U0001f4ca Research overall score: {scored_candidate.overall_score:.2f}/10",
    ]
    heuristic_dims = scored_candidate.scores.heuristic_dimensions
    real_dims = [d for d in SCORE_DIMENSIONS if d not in heuristic_dims]
    if real_dims:
        lines.append(f"\U0001f50e Confidence note: {len(real_dims)} scoring dimension(s) backed by real evidence, rest heuristic")
    else:
        lines.append("\U0001f50e Confidence note: scoring is currently fully heuristic (no real analytics data yet)")
    if candidate.hardware_tier:
        lines.append(f"\U0001f5a5️ Hardware tier: {candidate.hardware_tier.value}")
    lines.append("")
    lines.append(script.description)
    return "\n".join(lines)


def deliver_video(video_path: Path, script: VideoScript, scored_candidate: ScoredCandidate, client: TelegramClient, caption: str | None = None) -> dict:
    """Sends the video with its caption and Approve/Regenerate/Reject
    buttons. The buttons' callback_data carries `script.candidate_id` as
    the stable content ID (see models.py: populated from
    ResearchCandidate.candidate_id, which is required/non-empty) -- this is
    the same ID pipeline.py later polls for in telegram_approval.py, so a
    decision can always be mapped back to the exact generated video.
    """
    if not script.candidate_id:
        raise ValueError("VideoScript.candidate_id must be set before Telegram delivery (needed for approval callback_data)")

    if caption is None:
        caption = build_caption(script, scored_candidate)
    keyboard = build_approval_keyboard(script.candidate_id)
    logger.info("Sending %s to Telegram (content_id=%s)", video_path, script.candidate_id)
    result = client.send_video(video_path, caption, reply_markup=keyboard)
    logger.info("Telegram delivery succeeded (message_id=%s)", result.get("message_id"))
    return result


def build_batch_caption(
    script: VideoScript, scored_candidate: ScoredCandidate, *, candidate_number: int,
    batch_size: int, content_brief: ContentBrief, duration_seconds: float,
    real_media_coverage: float, qa_passed: bool,
) -> str:
    """Prepend the morning-review summary to the existing research caption."""
    lines = [
        f"🎬 Candidate {candidate_number}/{batch_size}",
        f"🎮 Topic: {script.topic}",
        f"👥 Target audience: {content_brief.target_audience}",
        f"🪝 Hook: {content_brief.selected_hook}",
        f"⏱ Duration: {duration_seconds:.1f}s",
        f"🎞 Real-media coverage: {real_media_coverage:.0%}",
        "✅ QA: passed" if qa_passed else "❌ QA: failed",
    ]
    return "\n".join(lines) + "\n\n" + build_caption(script, scored_candidate)
