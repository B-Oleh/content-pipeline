"""Overnight candidates sharing the production stages, with no publishing or polling."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from scripts.config import STATE_DIR
from scripts.production.asset_acquisition import AssetAcquisitionError, acquire_assets
from scripts.production.content_brief import ContentBrief, generate_content_brief
from scripts.production.models import VideoScript
from scripts.production.preflight import _safe_error_message
from scripts.production.providers.llm import GeminiProvider, _is_rate_limited
from scripts.production.providers.telegram_client import TelegramClient
from scripts.production.providers.visual import PexelsProvider, PixabayProvider
from scripts.production.providers.voice import EdgeTtsProvider
from scripts.production.qa import run_qa
from scripts.production.script_agent import generate_script
from scripts.production.subtitles import write_ass
from scripts.production.telegram_delivery import build_batch_caption, deliver_video
from scripts.production.topic_selection import NoSuitableCandidateError, select_topic_candidate
from scripts.production.video_assembly import VideoAssemblyError, render_video
from scripts.production.visual_quality import check_visual_quality, media_coverage_fraction
from scripts.production.voice_generation import VoiceGenerationError, generate_narration
from scripts.research.cli import run as run_research_agent
from scripts.utils.atomic_write import atomic_write_text
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)
BATCH_SIZE = 3


@dataclass
class BatchAttemptResult:
    attempt_number: int
    candidate_id: str
    topic: str
    candidate_number: int | None = None
    status: str = "failed_exception"
    failure_reason: str | None = None
    content_brief: ContentBrief | None = None
    script: VideoScript | None = None
    qa_passed: bool | None = None
    duration_seconds: float | None = None
    real_media_coverage: float | None = None
    video_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {field.name: getattr(self, field.name) for field in fields(self)}
        data["content_brief"] = self.content_brief.to_dict() if self.content_brief else None
        data["script"] = self.script.to_dict() if self.script else None
        return data


@dataclass
class BatchResult:
    batch_id: str
    generated_at: str
    batch_size: int
    attempts: list[BatchAttemptResult]
    completed: list[BatchAttemptResult]
    blocker: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "generated_at": self.generated_at,
            "batch_size": self.batch_size,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "completed": [attempt.to_dict() for attempt in self.completed],
            "blocker": self.blocker,
        }


def persist_batch_metadata(result: BatchResult, state_dir: Path | None = None) -> Path:
    """Atomically persist the same metadata as the transient diagnostics copy."""
    state_dir = Path(state_dir) if state_dir is not None else STATE_DIR / "production_batch"
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", result.batch_id)
    path = state_dir / f"batch_{safe_id}.json"
    atomic_write_text(path, json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return path


def _is_external_blocker(exc: Exception) -> bool:
    return (
        _is_rate_limited(exc)
        or type(exc).__module__.startswith("google.genai")
        or isinstance(exc, (
            requests.ConnectionError, requests.Timeout, requests.HTTPError,
            VoiceGenerationError, AssetAcquisitionError, VideoAssemblyError,
        ))
    )


def run_batch(
    *, gemini_api_key: str, pexels_api_key: str, pixabay_api_key: str,
    telegram_bot_token: str, telegram_chat_id: str, workdir: Path, output_dir: Path,
    research_config_path: Path | None = None, research_state_dir: Path | None = None,
    batch_size: int = BATCH_SIZE,
) -> BatchResult:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    batch_id = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    workdir, output_dir = Path(workdir), Path(output_dir)
    workdir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Stage: Research Agent")
    research_kwargs = {}
    if research_config_path is not None:
        research_kwargs["config_path"] = research_config_path
    if research_state_dir is not None:
        research_kwargs["state_dir"] = research_state_dir
    research_result = run_research_agent(**research_kwargs)
    remaining = list(research_result.candidates)
    llm_provider = GeminiProvider(gemini_api_key)
    visual_providers = [PexelsProvider(pexels_api_key), PixabayProvider(pixabay_api_key)]
    voice_provider = EdgeTtsProvider()
    telegram_client = TelegramClient(telegram_bot_token, telegram_chat_id)
    attempts: list[BatchAttemptResult] = []
    delivered: list[BatchAttemptResult] = []
    blocker = None

    while len(delivered) < batch_size and remaining and blocker is None:
        logger.info("Stage: Topic selection (attempt=%d)", len(attempts) + 1)
        try:
            scored = select_topic_candidate(replace(research_result, candidates=remaining))
        except NoSuitableCandidateError:
            attempts.append(BatchAttemptResult(
                len(attempts) + 1, "", "", failure_reason="no on-topic candidate remaining",
            ))
            blocker = "Research produced no more on-topic candidates to reach batch_size"
            logger.warning("Attempt %d: failed_exception; %s", len(attempts), blocker)
            break
        # Research normally deduplicates candidates; also enforce distinct IDs/topics here.
        remaining = [item for item in remaining if (
            item.candidate.candidate_id != scored.candidate.candidate_id
            and item.candidate.title.strip().casefold() != scored.candidate.title.strip().casefold()
        )]
        attempt = BatchAttemptResult(len(attempts) + 1, scored.candidate.candidate_id, scored.candidate.title)
        attempts.append(attempt)
        attempt_dir = workdir / "candidates" / f"candidate_{attempt.attempt_number}"
        try:
            attempt_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Stage: Content brief (attempt=%d)", attempt.attempt_number)
            brief = generate_content_brief(llm_provider, scored)
            attempt.content_brief = brief
            logger.info("Stage: Script Agent")
            script = generate_script(llm_provider, scored, content_brief=brief)
            attempt.script = script
            logger.info("Stage: Asset Acquisition")
            acquire_assets(script.scenes, visual_providers, attempt_dir, llm_provider)
            logger.info("Stage: Voice Generation")
            subtitle_cues = generate_narration(script.scenes, voice_provider, attempt_dir)
            (attempt_dir / "script.json").write_text(
                json.dumps(script.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
            )
            logger.info("Stage: Visual quality")
            quality = check_visual_quality(script.scenes)
            attempt.duration_seconds = sum(scene.duration_seconds or 0 for scene in script.scenes)
            attempt.real_media_coverage = media_coverage_fraction(script.scenes)
            if not quality.passed:
                attempt.status = "failed_visual"
                attempt.failure_reason = "; ".join(quality.summary_lines())
                continue
            logger.info("Stage: Subtitles")
            subtitle_path = attempt_dir / "captions.ass"
            write_ass(subtitle_cues, subtitle_path)
            logger.info("Stage: Video Assembly")
            video_path = render_video(
                script.scenes, output_dir / f"candidate_{len(delivered) + 1}.mp4",
                subtitle_path=subtitle_path,
            )
            attempt.video_path = str(video_path)
            logger.info("Stage: Automated QA")
            qa_result = run_qa(
                video_path, rendered_scene_count=len(script.scenes),
                narration_generated=all(scene.audio_path is not None for scene in script.scenes),
                subtitles_generated=len(subtitle_cues) > 0, scenes=script.scenes,
            )
            attempt.qa_passed = qa_result.passed
            (attempt_dir / "qa_result.json").write_text(
                json.dumps(qa_result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
            )
            if not qa_result.passed:
                attempt.status = "failed_qa"
                attempt.failure_reason = "; ".join(qa_result.summary_lines())
                continue
            logger.info("Stage: Telegram delivery")
            caption = build_batch_caption(
                script, scored, candidate_number=len(delivered) + 1, batch_size=batch_size,
                content_brief=brief, duration_seconds=attempt.duration_seconds,
                real_media_coverage=attempt.real_media_coverage, qa_passed=qa_result.passed,
            )
            deliver_video(video_path, script, scored, telegram_client, caption=caption)
            attempt.candidate_number = len(delivered) + 1
            attempt.status = "delivered"
            delivered.append(attempt)
        except Exception as exc:
            attempt.status = "failed_exception"
            attempt.failure_reason = _safe_error_message(exc)
            if _is_external_blocker(exc):
                blocker = attempt.failure_reason
        finally:
            logger.info("Attempt %d: %s%s", attempt.attempt_number, attempt.status,
                        f"; {attempt.failure_reason}" if attempt.failure_reason else "")

    if len(delivered) < batch_size and blocker is None:
        blocker = (f"produced {len(delivered)}/{batch_size} candidate(s); research candidates "
                   "exhausted before reaching batch_size")
    result = BatchResult(
        batch_id, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        batch_size, attempts, delivered, blocker,
    )
    logger.info("Stage: Batch metadata")
    (workdir / "batch_metadata.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )
    persist_batch_metadata(result)
    if delivered:
        recap = (
            f"📦 Batch complete: {len(delivered)}/{batch_size} candidate(s) delivered overnight.\n"
            "Review each video above and reply in the morning to pick which to publish.\n"
            "(No candidate was auto-selected.)"
        )
        if blocker:
            recap += f"\n⚠️ {blocker}"
    else:
        recap = f'⚠️ Batch produced no deliverable candidates.\nReason: {blocker or "unknown"}'
    logger.info("Stage: Telegram recap")
    try:
        telegram_client.send_message(recap)
    except Exception as exc:
        logger.warning("Could not send batch recap: %s", _safe_error_message(exc))
    return result
