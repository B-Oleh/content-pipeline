"""End-to-end orchestration: Research -> Script -> Assets -> Voice ->
Subtitles -> Render -> QA -> Telegram -> (bounded) approval wait.

See docs/PRODUCTION_PIPELINE.md for the full design. This module wires
together the existing Research Agent and the new production stages; each
stage's own module still owns its logic (see CLAUDE.md "Script
organization" -- shared orchestration only, no business logic duplicated
here).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scripts.production.asset_acquisition import acquire_assets
from scripts.production.models import QAResult, VideoScript
from scripts.production.providers.llm import GeminiProvider
from scripts.production.providers.telegram_client import TelegramClient
from scripts.production.providers.visual import PexelsProvider, PixabayProvider
from scripts.production.providers.voice import EdgeTtsProvider
from scripts.production.qa import run_qa
from scripts.production.script_agent import generate_script
from scripts.production.subtitles import write_ass
from scripts.production.telegram_approval import (
    REGENERATE_ACTION,
    ApprovalState,
    RegenerationTriggerError,
    poll_for_decision,
    save_approval_state,
    trigger_regeneration_workflow,
)
from scripts.production.telegram_delivery import deliver_video
from scripts.production.topic_selection import select_topic_candidate
from scripts.production.video_assembly import render_video
from scripts.production.voice_generation import generate_narration
from scripts.research.cli import run as run_research_agent
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineResult:
    script: VideoScript
    qa_result: QAResult
    video_path: Path
    delivered: bool
    approval: Optional[ApprovalState] = None


def run_pipeline(
    *,
    gemini_api_key: str,
    pexels_api_key: str,
    pixabay_api_key: str,
    telegram_bot_token: str,
    telegram_chat_id: str,
    workdir: Path,
    output_path: Path,
    research_config_path: Optional[Path] = None,
    research_state_dir: Optional[Path] = None,
    topic_override: Optional[str] = None,
    github_token: Optional[str] = None,
    github_repository: Optional[str] = None,
    approval_poll_timeout_seconds: Optional[float] = None,
) -> PipelineResult:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    logger.info("Stage: Research Agent")
    research_kwargs = {}
    if research_config_path is not None:
        research_kwargs["config_path"] = research_config_path
    if research_state_dir is not None:
        research_kwargs["state_dir"] = research_state_dir
    research_result = run_research_agent(**research_kwargs)
    scored_candidate = select_topic_candidate(research_result, preferred_title=topic_override)

    logger.info("Stage: Script Agent (Gemini)")
    llm_provider = GeminiProvider(gemini_api_key)
    script = generate_script(llm_provider, scored_candidate)
    (workdir / "script.json").write_text(json.dumps(script.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Stage: Asset Acquisition (Pexels/Pixabay + Gemini Vision)")
    visual_providers = [PexelsProvider(pexels_api_key), PixabayProvider(pixabay_api_key)]
    acquire_assets(script.scenes, visual_providers, workdir, llm_provider)

    logger.info("Stage: Voice Generation (edge-tts) + Subtitles")
    voice_provider = EdgeTtsProvider()
    subtitle_cues = generate_narration(script.scenes, voice_provider, workdir)
    subtitle_path = workdir / "captions.ass"
    write_ass(subtitle_cues, subtitle_path)

    logger.info("Stage: Video Assembly (ffmpeg)")
    video_path = render_video(script.scenes, Path(output_path), subtitle_path=subtitle_path)

    logger.info("Stage: Automated QA")
    qa_result = run_qa(
        video_path,
        rendered_scene_count=len(script.scenes),
        narration_generated=all(scene.audio_path is not None for scene in script.scenes),
        subtitles_generated=len(subtitle_cues) > 0,
    )
    (workdir / "qa_result.json").write_text(json.dumps(qa_result.to_dict(), indent=2), encoding="utf-8")

    if not qa_result.passed:
        logger.error("QA failed -- video will NOT be sent to Telegram: %s", qa_result.summary_lines())
        return PipelineResult(script=script, qa_result=qa_result, video_path=video_path, delivered=False)

    logger.info("Stage: Telegram delivery (with Approve/Regenerate/Reject buttons)")
    telegram_client = TelegramClient(telegram_bot_token, telegram_chat_id)
    deliver_video(video_path, script, scored_candidate, telegram_client)

    logger.info("Stage: Telegram approval wait (content_id=%s)", script.candidate_id)
    approval = _wait_for_approval(
        telegram_client,
        script,
        github_token=github_token,
        github_repository=github_repository,
        timeout_seconds=approval_poll_timeout_seconds,
    )
    save_approval_state(workdir / "approval_state.json", approval)

    return PipelineResult(script=script, qa_result=qa_result, video_path=video_path, delivered=True, approval=approval)


def _wait_for_approval(
    telegram_client: TelegramClient,
    script: VideoScript,
    *,
    github_token: Optional[str],
    github_repository: Optional[str],
    timeout_seconds: Optional[float],
) -> ApprovalState:
    """Waits (bounded -- see telegram_approval.py's module docstring for
    why) for an Approve/Regenerate/Reject decision, and actually acts on
    it: REGENERATE triggers a new produce_video.yml run for the same topic;
    APPROVE/REJECT/timeout just record state (no publishing exists yet to
    gate -- see CLAUDE.md "Telegram approval gate")."""
    poll_kwargs = {} if timeout_seconds is None else {"timeout_seconds": timeout_seconds}
    approval = poll_for_decision(telegram_client, script.candidate_id, **poll_kwargs)

    if approval.decision == REGENERATE_ACTION:
        if not github_token or not github_repository:
            detail = "GITHUB_TOKEN/GITHUB_REPOSITORY not available in this environment -- cannot trigger a new run automatically"
            logger.error("Regeneration requested but %s", detail)
            _safe_notify(telegram_client, f"⚠️ Regeneration requested, but {detail}. Please re-run the workflow manually.")
            return ApprovalState(content_id=approval.content_id, decision=REGENERATE_ACTION, detail=detail)
        try:
            trigger_regeneration_workflow(topic_title=script.topic, github_token=github_token, github_repository=github_repository)
            logger.info("Triggered a new produce_video.yml run for topic %r", script.topic)
            return ApprovalState(content_id=approval.content_id, decision=REGENERATE_ACTION, detail=f"triggered a new run for {script.topic!r}")
        except RegenerationTriggerError as exc:
            logger.error("Failed to trigger regeneration workflow: %s", exc)
            _safe_notify(telegram_client, "⚠️ Regeneration was requested, but triggering a new run failed. Please re-run the workflow manually.")
            return ApprovalState(content_id=approval.content_id, decision=REGENERATE_ACTION, detail=f"trigger failed: {exc}")

    return approval


def _safe_notify(telegram_client: TelegramClient, text: str) -> None:
    try:
        telegram_client.send_message(text)
    except Exception as exc:  # noqa: BLE001 -- a failed notification must not mask the original problem
        logger.warning("Could not send Telegram notification: %s", exc)
