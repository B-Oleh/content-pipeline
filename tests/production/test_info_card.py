"""info_card.py tests. The real-ffmpeg render is verified the same way as
video_assembly.py's integration test: skip gracefully if ffmpeg/ffprobe are
not on PATH, but actually render (and QA-check) a real MP4 when they are --
this exercises the exact drawtext/fontfile mechanics fixed after the local
"Fontconfig error: Cannot load default config file" failure (see
info_card.py's module docstring).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from scripts.production.info_card import _escape_drawtext, _find_font_file, render_info_card
from scripts.production.qa import run_qa

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed -- this test exercises the real renderer",
)


def test_escape_drawtext_handles_special_characters():
    assert _escape_drawtext("cost: $199") == "cost\\: $199"
    assert _escape_drawtext("it's here") == "it’s here"
    assert _escape_drawtext("50% faster") == "50\\% faster"


def test_find_font_file_returns_an_existing_path_or_none():
    result = _find_font_file()
    assert result is None or __import__("pathlib").Path(result).exists()


def test_render_info_card_with_flat_background(tmp_path):
    dest = tmp_path / "card.mp4"
    result = render_info_card("Test Headline", "A short supporting fact.", dest)

    assert result == dest
    assert dest.exists()
    assert dest.stat().st_size > 0

    qa_result = run_qa(dest, rendered_scene_count=1, narration_generated=True, subtitles_generated=False)
    assert qa_result.checks["has_video_stream"]
    assert qa_result.checks["resolution_1080x1920"]


def test_render_info_card_with_photo_background(tmp_path):
    photo_path = tmp_path / "bg.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=800x600", "-frames:v", "1", str(photo_path)],
        capture_output=True, text=True, timeout=30, check=True,
    )

    dest = tmp_path / "card_photo.mp4"
    render_info_card("Photo Backed Card", "Fact goes here", dest, background_path=photo_path, background_is_video=False)

    assert dest.exists()
    qa_result = run_qa(dest, rendered_scene_count=1, narration_generated=True, subtitles_generated=False)
    assert qa_result.checks["has_video_stream"]
    assert qa_result.checks["resolution_1080x1920"]


def test_render_info_card_with_video_background(tmp_path):
    video_path = tmp_path / "bg.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=640x360:d=6", "-pix_fmt", "yuv420p", str(video_path)],
        capture_output=True, text=True, timeout=30, check=True,
    )

    dest = tmp_path / "card_video.mp4"
    render_info_card("Video Backed Card", "", dest, background_path=video_path, background_is_video=True)

    assert dest.exists()
    qa_result = run_qa(dest, rendered_scene_count=1, narration_generated=True, subtitles_generated=False)
    assert qa_result.checks["has_video_stream"]


def test_render_info_card_truncates_overly_long_text(tmp_path):
    dest = tmp_path / "card_long.mp4"
    headline = "H" * 500
    fact = "F" * 500
    # Must not fail even with pathological input lengths.
    render_info_card(headline, fact, dest)
    assert dest.exists()


def test_render_info_card_handles_empty_key_fact(tmp_path):
    dest = tmp_path / "card_no_fact.mp4"
    render_info_card("Only a headline", "", dest)
    assert dest.exists()
