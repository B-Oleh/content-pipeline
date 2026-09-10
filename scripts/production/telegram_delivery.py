"""Telegram delivery: sends the rendered MP4 with a research-grounded caption.

See CLAUDE.md pipeline stage 10 ("Telegram approval gate") and the task's
explicit scope for this milestone: no Approve/Regenerate/Reject buttons yet
-- only proving the real video reaches Telegram. QA must have passed before
this is ever called (see pipeline.py); this module does not re-check QA.
"""

from __future__ import annotations

from pathlib import Path

from scripts.production.models import VideoScript
from scripts.production.providers.telegram_client import TelegramClient
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


def deliver_video(video_path: Path, script: VideoScript, scored_candidate: ScoredCandidate, client: TelegramClient) -> dict:
    caption = build_caption(script, scored_candidate)
    logger.info("Sending %s to Telegram", video_path)
    result = client.send_video(video_path, caption)
    logger.info("Telegram delivery succeeded (message_id=%s)", result.get("message_id"))
    return result
