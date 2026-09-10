"""Automated QA: deterministic ffprobe-based checks before Telegram delivery.

See CLAUDE.md pipeline stage 9 and the quality control checklist. If any
required check fails, QAResult.passed is False and the pipeline must not
send a success message (see telegram_delivery.py / pipeline.py) -- "fail
loudly" per CLAUDE.md, not a silently-broken video reaching the channel.
"""

from __future__ import annotations

from pathlib import Path

from scripts.production.ffmpeg_utils import probe_streams
from scripts.production.models import QAResult
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

EXPECTED_WIDTH = 1080
EXPECTED_HEIGHT = 1920
MIN_DURATION_SECONDS = 15.0
MAX_DURATION_SECONDS = 90.0
MIN_SCENE_COUNT = 3


def run_qa(
    video_path: Path,
    *,
    rendered_scene_count: int,
    narration_generated: bool,
    subtitles_generated: bool,
) -> QAResult:
    video_path = Path(video_path)
    checks: dict[str, bool] = {}
    details: dict[str, str] = {}

    checks["file_exists"] = video_path.exists()
    if not checks["file_exists"]:
        details["file_exists"] = f"{video_path} does not exist"
        return QAResult(passed=False, checks=checks, details=details)

    size = video_path.stat().st_size
    checks["non_zero_size"] = size > 0
    details["non_zero_size"] = f"{size} bytes"
    if not checks["non_zero_size"]:
        return QAResult(passed=False, checks=checks, details=details)

    try:
        info = probe_streams(video_path)
    except Exception as exc:  # noqa: BLE001 -- a probe failure is itself a QA failure, not a crash
        checks["ffprobe_readable"] = False
        details["ffprobe_readable"] = str(exc)
        return QAResult(passed=False, checks=checks, details=details)
    checks["ffprobe_readable"] = True

    video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    audio_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]

    checks["has_video_stream"] = len(video_streams) > 0
    checks["has_audio_stream"] = len(audio_streams) > 0

    if video_streams:
        width = video_streams[0].get("width")
        height = video_streams[0].get("height")
        checks["resolution_1080x1920"] = (width == EXPECTED_WIDTH and height == EXPECTED_HEIGHT)
        details["resolution_1080x1920"] = f"got {width}x{height}"
    else:
        checks["resolution_1080x1920"] = False
        details["resolution_1080x1920"] = "no video stream"

    duration = float(info.get("format", {}).get("duration", 0.0))
    checks["duration_in_range"] = MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS
    details["duration_in_range"] = f"{duration:.1f}s (expected {MIN_DURATION_SECONDS:.0f}-{MAX_DURATION_SECONDS:.0f}s)"

    checks["required_scenes_rendered"] = rendered_scene_count >= MIN_SCENE_COUNT
    details["required_scenes_rendered"] = f"{rendered_scene_count} scene(s) (minimum {MIN_SCENE_COUNT})"

    checks["narration_generated"] = narration_generated
    checks["subtitles_generated"] = subtitles_generated

    passed = all(checks.values())
    result = QAResult(passed=passed, checks=checks, details=details)
    if passed:
        logger.info("QA passed: %s", video_path)
    else:
        logger.error("QA FAILED for %s: %s", video_path, [name for name, ok in checks.items() if not ok])
    return result
