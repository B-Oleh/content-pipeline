"""End-to-end orchestration: Research -> Script -> Assets -> Voice ->
Subtitles -> Render -> QA -> Telegram.

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
    scored_candidate = select_topic_candidate(research_result)

    logger.info("Stage: Script Agent (Gemini)")
    llm_provider = GeminiProvider(gemini_api_key)
    script = generate_script(llm_provider, scored_candidate)
    (workdir / "script.json").write_text(json.dumps(script.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Stage: Asset Acquisition (Pexels/Pixabay)")
    visual_providers = [PexelsProvider(pexels_api_key), PixabayProvider(pixabay_api_key)]
    acquire_assets(script.scenes, visual_providers, workdir)

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

    logger.info("Stage: Telegram delivery")
    telegram_client = TelegramClient(telegram_bot_token, telegram_chat_id)
    deliver_video(video_path, script, scored_candidate, telegram_client)

    return PipelineResult(script=script, qa_result=qa_result, video_path=video_path, delivered=True)
