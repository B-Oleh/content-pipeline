"""Small shared helpers for shelling out to ffmpeg/ffprobe.

Used by voice_generation.py (to measure exact narration duration so each
scene's visual is trimmed to precisely match its own audio -- see
video_assembly.py), video_assembly.py itself, and qa.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class FfmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg/ffprobe is not installed / not on PATH."""


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FfmpegNotFoundError(f"{name} is not installed or not on PATH -- see docs/PRODUCTION_PIPELINE.md")
    return path


def probe_duration_seconds(path: Path) -> float:
    """Exact duration of a media file, via ffprobe."""
    ffprobe = require_binary("ffprobe")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def probe_streams(path: Path) -> dict:
    """Full ffprobe format+stream info as a dict, for QA checks."""
    ffprobe = require_binary("ffprobe")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    return json.loads(result.stdout)
