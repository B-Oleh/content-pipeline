"""info_card.py tests.

Layout tests (wrapping, line caps, font-size reduction, safe margins) run
as pure Python against Pillow -- no ffmpeg needed, since that is where the
real layout logic now lives (see info_card.py's module docstring: the old
single-line `drawtext` call had no wrap/shrink capability at all, which is
the bug this module fixes). The real-ffmpeg render is verified the same
way as video_assembly.py's integration test: skip gracefully if ffmpeg/
ffprobe are not on PATH, but actually render (and QA-check) a real MP4
when they are.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from scripts.production.info_card import (
    BODY_FONT_SIZE_MAX,
    BODY_FONT_SIZE_MIN,
    BODY_MAX_LINES,
    CARD_HEIGHT,
    CARD_WIDTH,
    MARGIN_H,
    MARGIN_V,
    TITLE_FONT_SIZE_MAX,
    TITLE_FONT_SIZE_MIN,
    TITLE_MAX_LINES,
    _block_height,
    _fit_text_block,
    _find_font_file,
    _render_text_layer,
    render_info_card,
)
from scripts.production.qa import run_qa

# Representative examples from a real generated video's broken overlay
# (long title cut off at the right edge, body text not wrapped).
LONG_TITLE = "Myth 2: You Need The Top Flagship GPU"
LONG_BODY = "You do not need to buy the expensive flagship card to play modern titles smoothly"

SAFE_WIDTH = CARD_WIDTH - 2 * MARGIN_H


def _draw() -> ImageDraw.ImageDraw:
    return ImageDraw.Draw(Image.new("RGBA", (1, 1)))


def test_find_font_file_returns_an_existing_path_or_none():
    result = _find_font_file()
    assert result is None or __import__("pathlib").Path(result).exists()


# ---------------------------------------------------------------------------
# 1 & 3: long title wraps and respects the max-3-lines cap, fully inside the frame
# ---------------------------------------------------------------------------


def test_long_title_wraps_and_stays_within_the_safe_width():
    draw = _draw()
    font_path = _find_font_file()

    lines, font = _fit_text_block(
        draw, LONG_TITLE, font_path,
        max_width=SAFE_WIDTH, max_lines=TITLE_MAX_LINES,
        font_size_max=TITLE_FONT_SIZE_MAX, font_size_min=TITLE_FONT_SIZE_MIN,
    )

    assert len(lines) > 1  # it actually wrapped, not one overflowing line
    for line in lines:
        assert draw.textlength(line, font=font) <= SAFE_WIDTH


def test_title_never_exceeds_max_three_lines_even_for_much_longer_text():
    draw = _draw()
    font_path = _find_font_file()
    very_long_title = "Myth 2: You Absolutely Definitely Positively Need The Top Flagship GPU To Play Every Modern Game Smoothly At Maximum Settings"

    lines, font = _fit_text_block(
        draw, very_long_title, font_path,
        max_width=SAFE_WIDTH, max_lines=TITLE_MAX_LINES,
        font_size_max=TITLE_FONT_SIZE_MAX, font_size_min=TITLE_FONT_SIZE_MIN,
    )

    assert len(lines) <= TITLE_MAX_LINES
    for line in lines:
        assert draw.textlength(line, font=font) <= SAFE_WIDTH


# ---------------------------------------------------------------------------
# 2 & 4: long body wraps and respects the max-4-lines cap, fully inside the frame
# ---------------------------------------------------------------------------


def test_long_body_wraps_and_stays_within_the_safe_width():
    draw = _draw()
    font_path = _find_font_file()

    lines, font = _fit_text_block(
        draw, LONG_BODY, font_path,
        max_width=SAFE_WIDTH, max_lines=BODY_MAX_LINES,
        font_size_max=BODY_FONT_SIZE_MAX, font_size_min=BODY_FONT_SIZE_MIN,
    )

    assert len(lines) > 1
    for line in lines:
        assert draw.textlength(line, font=font) <= SAFE_WIDTH


def test_body_never_exceeds_max_four_lines_even_for_much_longer_text():
    draw = _draw()
    font_path = _find_font_file()
    very_long_body = (
        "You absolutely do not need to buy the most expensive top of the line flagship graphics "
        "card just to be able to play every single modern triple-A game title smoothly at a "
        "perfectly acceptable and enjoyable frame rate on a normal everyday monitor"
    )

    lines, font = _fit_text_block(
        draw, very_long_body, font_path,
        max_width=SAFE_WIDTH, max_lines=BODY_MAX_LINES,
        font_size_max=BODY_FONT_SIZE_MAX, font_size_min=BODY_FONT_SIZE_MIN,
    )

    assert len(lines) <= BODY_MAX_LINES
    for line in lines:
        assert draw.textlength(line, font=font) <= SAFE_WIDTH


# ---------------------------------------------------------------------------
# 5: font size is reduced when wrapped text still does not fit at the max size
# ---------------------------------------------------------------------------


def test_font_size_is_reduced_for_text_that_would_otherwise_exceed_max_lines():
    draw = _draw()
    font_path = _find_font_file()
    # Long enough that at TITLE_FONT_SIZE_MAX it would wrap to more than
    # TITLE_MAX_LINES lines, forcing the fitter to shrink the font.
    long_title = "Myth 2: You Absolutely Definitely Positively Need The Top Flagship GPU To Play Every Modern Game"

    lines, font = _fit_text_block(
        draw, long_title, font_path,
        max_width=SAFE_WIDTH, max_lines=TITLE_MAX_LINES,
        font_size_max=TITLE_FONT_SIZE_MAX, font_size_min=TITLE_FONT_SIZE_MIN,
    )

    assert font.size < TITLE_FONT_SIZE_MAX
    assert len(lines) <= TITLE_MAX_LINES


def test_short_text_keeps_the_maximum_font_size():
    draw = _draw()
    font_path = _find_font_file()

    lines, font = _fit_text_block(
        draw, "Worth knowing", font_path,
        max_width=SAFE_WIDTH, max_lines=TITLE_MAX_LINES,
        font_size_max=TITLE_FONT_SIZE_MAX, font_size_min=TITLE_FONT_SIZE_MIN,
    )

    assert font.size == TITLE_FONT_SIZE_MAX
    assert len(lines) == 1


def test_pathologically_long_text_is_truncated_with_an_ellipsis_not_overflowed():
    draw = _draw()
    font_path = _find_font_file()
    huge_title = "Myth " + ("Extremely Important Flagship Graphics Card Performance " * 10)

    lines, font = _fit_text_block(
        draw, huge_title, font_path,
        max_width=SAFE_WIDTH, max_lines=TITLE_MAX_LINES,
        font_size_max=TITLE_FONT_SIZE_MAX, font_size_min=TITLE_FONT_SIZE_MIN,
    )

    assert len(lines) <= TITLE_MAX_LINES
    assert font.size == TITLE_FONT_SIZE_MIN
    assert lines[-1].endswith("…")
    for line in lines:
        assert draw.textlength(line, font=font) <= SAFE_WIDTH


# ---------------------------------------------------------------------------
# 6 & 8: the full text layer (title + body) never renders outside 1080x1920,
# including the vertical safe margins, for the exact task example strings
# ---------------------------------------------------------------------------


def test_full_text_layer_is_exactly_frame_sized_and_uses_rgba():
    font_path = _find_font_file()
    layer = _render_text_layer(LONG_TITLE, LONG_BODY, font_path)

    assert layer.size == (CARD_WIDTH, CARD_HEIGHT)
    assert layer.mode == "RGBA"


def test_title_and_body_block_together_fit_within_the_vertical_safe_area():
    """Even at the worst case (both blocks maxed out at MAX_LINES, minimum
    font size), the combined title+spacing+body block must fit inside the
    vertical safe area -- otherwise the vertical centering in
    _render_text_layer() could push text past the top/bottom margins."""
    from scripts.production.info_card import BLOCK_SPACING_PX, _load_font

    font_path = _find_font_file()
    title_font = _load_font(font_path, TITLE_FONT_SIZE_MIN)
    body_font = _load_font(font_path, BODY_FONT_SIZE_MIN)
    worst_case_height = (
        _block_height([""] * TITLE_MAX_LINES, title_font) + BLOCK_SPACING_PX + _block_height([""] * BODY_MAX_LINES, body_font)
    )

    available_height = CARD_HEIGHT - 2 * MARGIN_V
    assert worst_case_height <= available_height


def test_no_line_in_the_rendered_layer_exceeds_the_safe_width_for_the_task_examples():
    draw = _draw()
    font_path = _find_font_file()

    title_lines, title_font = _fit_text_block(
        draw, LONG_TITLE, font_path,
        max_width=SAFE_WIDTH, max_lines=TITLE_MAX_LINES,
        font_size_max=TITLE_FONT_SIZE_MAX, font_size_min=TITLE_FONT_SIZE_MIN,
    )
    body_lines, body_font = _fit_text_block(
        draw, LONG_BODY, font_path,
        max_width=SAFE_WIDTH, max_lines=BODY_MAX_LINES,
        font_size_max=BODY_FONT_SIZE_MAX, font_size_min=BODY_FONT_SIZE_MIN,
    )

    for line in title_lines:
        assert draw.textlength(line, font=title_font) <= SAFE_WIDTH
    for line in body_lines:
        assert draw.textlength(line, font=body_font) <= SAFE_WIDTH


# ---------------------------------------------------------------------------
# 7: real ffmpeg rendering path still works
#
# Only these tests need real ffmpeg/ffprobe -- the layout tests above are
# pure Python and always run. A per-function marker (not a module-level
# `pytestmark`) is used deliberately so ffmpeg's absence never skips the
# layout-correctness tests too.
# ---------------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed -- this exercises the real renderer",
)


@needs_ffmpeg
def test_render_info_card_with_flat_background(tmp_path):
    dest = tmp_path / "card.mp4"
    result = render_info_card("Test Headline", "A short supporting fact.", dest)

    assert result == dest
    assert dest.exists()
    assert dest.stat().st_size > 0

    qa_result = run_qa(dest, rendered_scene_count=1, narration_generated=True, subtitles_generated=False)
    assert qa_result.checks["has_video_stream"]
    assert qa_result.checks["resolution_1080x1920"]


@needs_ffmpeg
def test_render_info_card_with_long_title_and_body_still_renders_correct_resolution(tmp_path):
    """The exact real-world failure this fixes: a long title/body must
    still render at the correct resolution with no ffmpeg errors -- proving
    the overlay compositing path (not just the pure-layout math above)
    works end to end."""
    dest = tmp_path / "card_long_real.mp4"
    render_info_card(LONG_TITLE, LONG_BODY, dest)

    assert dest.exists()
    qa_result = run_qa(dest, rendered_scene_count=1, narration_generated=True, subtitles_generated=False)
    assert qa_result.checks["has_video_stream"]
    assert qa_result.checks["resolution_1080x1920"]


@needs_ffmpeg
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


@needs_ffmpeg
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


@needs_ffmpeg
def test_render_info_card_truncates_overly_long_text(tmp_path):
    dest = tmp_path / "card_long.mp4"
    headline = "H" * 500
    fact = "F" * 500
    # Must not fail even with pathological input lengths.
    render_info_card(headline, fact, dest)
    assert dest.exists()


@needs_ffmpeg
def test_render_info_card_handles_empty_key_fact(tmp_path):
    dest = tmp_path / "card_no_fact.mp4"
    render_info_card("Only a headline", "", dest)
    assert dest.exists()
