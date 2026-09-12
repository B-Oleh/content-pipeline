"""Video Assembly: renders scenes + narration + subtitles into one MP4.

See CLAUDE.md pipeline stage 8. FFmpeg is the renderer (see
docs/PRODUCTION_PIPELINE.md "Video"). Each scene's visual is trimmed/looped
to exactly its own narration's duration (see voice_generation.py) and paired
with that narration in a single filter_complex concat -- this is what keeps
video and audio in sync and avoids silent gaps, without a separate alignment
step. No background music (see task requirement: keep the first working
pipeline simple).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from scripts.production.ffmpeg_utils import require_binary
from scripts.production.models import Scene
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30


class VideoAssemblyError(RuntimeError):
    pass


def _escape_filter_path(path: Path) -> str:
    """Escape a filesystem path for embedding inside an ffmpeg filter value.

    Forward slashes avoid Windows backslash-escaping headaches; the colon
    after a Windows drive letter must be escaped or ffmpeg parses it as a
    filter-option separator.
    """
    text = str(path).replace("\\", "/")
    return text.replace(":", "\\:")


def _build_subtitle_filter(subtitle_path: Path) -> str:
    """The subtitles-filter fragment burning captions onto [vconcat].

    No force_style/original_size here: subtitle_path is an .ass file (see
    subtitles.py) that already declares its own PlayResX/PlayResY matching
    this module's TARGET_WIDTH/TARGET_HEIGHT and its own Style (font size,
    margins, colors, outline). Font-size/margin pixel values are therefore
    applied 1:1 against the real frame -- see subtitles.py's module
    docstring for why relying on ffmpeg's automatic SRT->ASS conversion
    plus force_style/original_size instead produced captions rendered
    several times larger than requested ("subtitles are too large" bug).
    """
    escaped = _escape_filter_path(Path(subtitle_path))
    return f"subtitles='{escaped}'"


def render_video(scenes: list[Scene], output_path: Path, subtitle_path: Optional[Path] = None) -> Path:
    """Render scenes (each with asset_path/asset_is_video/audio_path/duration_seconds
    already set -- see asset_acquisition.py and voice_generation.py) into one
    1080x1920 H.264/AAC MP4 at output_path.
    """
    ffmpeg = require_binary("ffmpeg")

    if not scenes:
        raise VideoAssemblyError("No scenes to render")
    for scene in scenes:
        if not scene.asset_path or not Path(scene.asset_path).exists():
            raise VideoAssemblyError(f"Scene {scene.index} has no downloaded visual asset")
        if not scene.audio_path or not Path(scene.audio_path).exists():
            raise VideoAssemblyError(f"Scene {scene.index} has no narration audio")
        if not scene.duration_seconds or scene.duration_seconds <= 0:
            raise VideoAssemblyError(f"Scene {scene.index} has no valid narration duration")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    input_args: list[str] = []
    filter_parts: list[str] = []
    concat_refs: list[str] = []

    for i, scene in enumerate(scenes):
        duration = f"{scene.duration_seconds:.3f}"
        if scene.asset_is_video:
            input_args += ["-stream_loop", "-1", "-t", duration, "-i", str(scene.asset_path)]
        else:
            input_args += ["-loop", "1", "-t", duration, "-i", str(scene.asset_path)]
        input_args += ["-i", str(scene.audio_path)]

        video_input_idx = 2 * i
        audio_input_idx = 2 * i + 1

        video_filter = (
            f"[{video_input_idx}:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},fps={TARGET_FPS},format=yuv420p,setsar=1"
        )
        if not scene.asset_is_video:
            # A static photo would otherwise sit completely still for its
            # whole scene -- a mild continuous zoom keeps it visually alive.
            frames = max(int(scene.duration_seconds * TARGET_FPS), 1)
            video_filter += f",zoompan=z='min(zoom+0.0006,1.12)':d={frames}:s={TARGET_WIDTH}x{TARGET_HEIGHT}:fps={TARGET_FPS}"
        video_filter += f",trim=duration={duration},setpts=PTS-STARTPTS[v{i}]"
        filter_parts.append(video_filter)

        filter_parts.append(f"[{audio_input_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim=duration={duration},asetpts=PTS-STARTPTS[a{i}]")

        concat_refs.append(f"[v{i}][a{i}]")

    filter_parts.append("".join(concat_refs) + f"concat=n={len(scenes)}:v=1:a=1[vconcat][aout]")

    video_map = "[vconcat]"
    if subtitle_path is not None:
        filter_parts.append(f"[vconcat]{_build_subtitle_filter(subtitle_path)}[vsub]")
        video_map = "[vsub]"

    filter_complex = ";".join(filter_parts)

    cmd = [
        ffmpeg,
        "-y",
        *input_args,
        "-filter_complex",
        filter_complex,
        "-map",
        video_map,
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(TARGET_FPS),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    logger.info("Rendering %d scene(s) with ffmpeg -> %s", len(scenes), output_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise VideoAssemblyError(f"ffmpeg failed (exit {result.returncode}): {result.stderr[-4000:]}")

    logger.info("Rendered %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path
