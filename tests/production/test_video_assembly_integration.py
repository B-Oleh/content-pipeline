"""The most important test in this milestone: can the pipeline actually
render a real MP4 and have it pass QA?

Uses ffmpeg's own lavfi test sources (color bars + tone) instead of real
downloaded stock footage/narration, so this proves video_assembly.py and
qa.py work correctly without any network access or API keys -- exactly the
part of the pipeline that does NOT depend on credentials.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from scripts.production.models import Scene, SubtitleCue
from scripts.production.qa import run_qa
from scripts.production.subtitles import write_ass
from scripts.production.video_assembly import render_video

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed -- this test exercises the real renderer, see docs/PRODUCTION_PIPELINE.md",
)


def _make_synthetic_video(path, duration: float, color: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s=640x360:d={duration}",
            "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, text=True, timeout=60, check=True,
    )


def _make_synthetic_audio(path, duration: float, frequency: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration={duration}",
            "-q:a", "4", str(path),
        ],
        capture_output=True, text=True, timeout=60, check=True,
    )


def test_render_and_qa_a_real_synthetic_video(tmp_path):
    scenes = []
    colors = ["red", "green", "blue"]
    durations = [6.137, 6.612, 7.081]
    for i, color in enumerate(colors):
        video_path = tmp_path / f"asset_{i}.mp4"
        audio_path = tmp_path / f"audio_{i}.mp3"
        duration = durations[i]
        _make_synthetic_video(video_path, duration, color)
        _make_synthetic_audio(audio_path, duration, frequency=440 + i * 110)

        scenes.append(
            Scene(
                index=i,
                narration_line=f"Scene {i} narration.",
                asset_path=video_path,
                asset_is_video=True,
                # Synthetic fixtures stand in for accepted provider downloads.
                asset_source="pexels",
                media_accepted=True,
                production_mode="real_visual",
                audio_path=audio_path,
                duration_seconds=duration,
            )
        )

    subtitle_path = tmp_path / "captions.ass"
    write_ass(
        [
            SubtitleCue(index=1, start=0.0, end=1.0, text="Scene zero."),
            SubtitleCue(index=2, start=2.5, end=3.5, text="Scene one."),
        ],
        subtitle_path,
    )

    output_path = tmp_path / "final_video.mp4"
    result_path = render_video(scenes, output_path, subtitle_path=subtitle_path)

    assert result_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0

    qa_result = run_qa(
        output_path,
        rendered_scene_count=len(scenes),
        narration_generated=True,
        subtitles_generated=True,
        scenes=scenes,
    )

    assert qa_result.passed, qa_result.summary_lines()
    assert qa_result.checks["resolution_1080x1920"]
    assert qa_result.checks["has_video_stream"]
    assert qa_result.checks["has_audio_stream"]
    assert qa_result.checks["duration_in_range"]
    assert qa_result.checks["scene_timeline_matches_render"]
    assert qa_result.checks["real_media_coverage"]
    assert qa_result.checks["info_card_streak"]


def _make_synthetic_photo(path, color: str = "blue") -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s=800x600",
            "-frames:v", "1", str(path),
        ],
        capture_output=True, text=True, timeout=60, check=True,
    )


def test_render_with_a_static_image_scene(tmp_path):
    """Covers the photo-fallback path (Ken Burns zoompan) end to end."""
    image_path = tmp_path / "photo.jpg"
    _make_synthetic_photo(image_path)

    audio_path = tmp_path / "audio.mp3"
    _make_synthetic_audio(audio_path, duration=2.0, frequency=300)

    scene = Scene(
        index=0,
        narration_line="A static photo scene.",
        asset_path=image_path,
        asset_is_video=False,
        audio_path=audio_path,
        duration_seconds=2.0,
    )

    output_path = tmp_path / "final_video.mp4"
    render_video([scene], output_path)

    qa_result = run_qa(output_path, rendered_scene_count=1, narration_generated=True, subtitles_generated=False)
    assert qa_result.checks["has_video_stream"]
    assert qa_result.checks["has_audio_stream"]
    assert qa_result.checks["resolution_1080x1920"]

    from scripts.production.ffmpeg_utils import probe_duration_seconds
    assert abs(probe_duration_seconds(output_path) - 2.0) < 0.1
