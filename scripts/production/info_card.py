"""Renders designed, motion-enhanced video clips for scenes where no (or
no strong) honestly-matching stock asset exists (see
docs/PRODUCTION_PIPELINE.md "Visual relevance" and the task's explicit
"never imply generic stock footage is footage of a specific named game,
GPU, laptop, or product" requirement).

Two public entry points, sharing the same Pillow text-layout engine and
ffmpeg compositing pipeline:
- `render_info_card()` -- a full designed card (headline + short support
  line) over a subtle gradient/tech background when no real footage is
  usable at all (`scene.production_mode = "info_card"`).
- `render_hybrid_scene()` -- a REQUIRED real photo/video background with a
  smaller, top-anchored overlay card, for a scene whose best visual match
  is honest and on-topic but not a strong/exact one
  (`scene.production_mode = "hybrid_visual"` -- see asset_acquisition.py).

Text (title/headline + supporting body text) is laid out with Pillow, not
ffmpeg's `drawtext` filter, and composited onto the background via
ffmpeg's `overlay` filter. `drawtext` has no word-wrap of its own -- a long
headline was rendered as one single line via `x=(w-text_w)/2`, and once
`text_w` exceeded the frame width the line simply ran off the right edge
(there is no ffmpeg option to auto-wrap or auto-shrink `drawtext`). Pillow
gives real text measurement (`ImageDraw.textlength`/`textbbox`), which is
what a real layout system (wrapping, line caps, font-size reduction,
intelligent truncation, safe margins) needs -- see `_fit_text_block()`.

Motion is deterministic and lightweight, never re-encoded per-frame in
Python: a slow background zoom (existing `zoompan`, flat/photo
backgrounds only -- a video background already has its own motion), plus a
fade-in + slide-up on the text layer itself via ffmpeg's `fade` filter and
a time-varying `overlay` y-expression (see `_composite_card()`).

Both entry points still produce an ordinary .mp4 file, so they plug into
asset_acquisition.py and video_assembly.py exactly like a downloaded stock
video: video_assembly.py already loops/trims any video asset to the
scene's real narration duration (-stream_loop -1 -t <duration>), so a
card's own rendered duration only needs to be long enough to loop
smoothly, not match the scene exactly.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from scripts.production.ffmpeg_utils import require_binary
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

CARD_WIDTH = 1080
CARD_HEIGHT = 1920
CARD_FPS = 30
# video_assembly.py loops/trims any video asset to the real scene duration
# anyway -- this only needs to be long enough for the zoom/backdrop to read
# smoothly on a loop.
CARD_DURATION_SECONDS = 6.0
HEADLINE_MAX_CHARS = 70
FACT_MAX_CHARS = 90

# Safe margins the text layout must never cross (task's explicit "left/right
# padding ~80-100px, top/bottom padding ~100-140px" requirement).
MARGIN_H = 90
MARGIN_V = 120

TITLE_MAX_LINES = 3
# "Headline + one short supporting statement, not a paragraph" -- see the
# task's explicit "prefer headline + one short supporting statement
# instead of paragraph-style text" requirement.
BODY_MAX_LINES = 2

TITLE_FONT_SIZE_MAX = 76
TITLE_FONT_SIZE_MIN = 38
BODY_FONT_SIZE_MAX = 46
BODY_FONT_SIZE_MIN = 26
FONT_SIZE_STEP = 2
LINE_SPACING_RATIO = 1.28  # line box height as a multiple of font size
BLOCK_SPACING_PX = 56  # gap between the title block and the body block

# A soft, rounded card panel behind the text so it stays legible over any
# background (photo, video, or flat color) -- this is the "feel visually
# modern and clean" requirement, not just bare text on the frame.
PANEL_PADDING_H = 48
PANEL_PADDING_V = 36
PANEL_RADIUS = 28
PANEL_FILL = (8, 8, 14, 178)
PANEL_MIN_MARGIN = 40  # the panel itself may sit slightly outside the text safe margin, but never past this

TITLE_COLOR = (255, 255, 255, 255)
BODY_COLOR = (223, 223, 230, 255)
SHADOW_COLOR = (0, 0, 0, 160)
SHADOW_OFFSET = 3
ELLIPSIS = "\u2026"

# Compact, top-anchored layout for render_hybrid_scene()'s overlay card --
# deliberately smaller/shorter than the full info-card layout, since real
# footage already fills most of the frame (the task's example: "MYTH 2" /
# "You need a flagship GPU" as a small overlay near the top, not a
# full-screen text block).
HYBRID_TITLE_MAX_LINES = 2
HYBRID_BODY_MAX_LINES = 1
HYBRID_TITLE_FONT_SIZE_MAX = 58
HYBRID_TITLE_FONT_SIZE_MIN = 34
HYBRID_BODY_FONT_SIZE_MAX = 38
HYBRID_BODY_FONT_SIZE_MIN = 24
HYBRID_TOP_MARGIN = 140  # anchors the overlay in the upper third, clear of any burned-in subtitles below

# Gradient "tech" background used by render_info_card() when no real photo/
# video background is given -- a flat solid color reads as an unfinished
# placeholder; a gradient + soft glow accents reads as a designed scene.
GRADIENT_TOP_COLOR = (16, 18, 34)
GRADIENT_BOTTOM_COLOR = (8, 9, 18)
GLOW_COLOR = (70, 100, 170)
GLOW_BLUR_RADIUS = 140

# Deterministic, lightweight text motion (see module docstring) -- a fixed
# short fade-in plus a small upward slide, never per-frame Python
# rendering.
TEXT_FADE_IN_SECONDS = 0.5
TEXT_SLIDE_UP_PX = 40

# ffmpeg's `subtitles`/libass filter resolves its own fonts; this module
# (both the old drawtext path and the current Pillow path) needs a real
# font *file* -- on a machine where fontconfig's own config file is
# missing (observed locally on Windows: "Fontconfig error: Cannot load
# default config file"), relying on a font *name* fails outright. The
# GitHub Actions workflow explicitly installs a Linux font package
# alongside ffmpeg so one of the Linux paths below is always present there
# -- see produce_video.yml.
_FONT_FILE_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


class InfoCardError(RuntimeError):
    pass


def _find_font_file() -> Optional[str]:
    for candidate in _FONT_FILE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _load_font(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    """Loads a real scalable font at `size`. Falls back to Pillow's own
    built-in scalable font if no system font file was found, so a missing
    font never turns into a hard failure -- only an uglier (but still
    correctly laid out, non-clipping) render."""
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            logger.warning("Could not load font file %s at size %d -- falling back to Pillow's default font", font_path, size)
    return ImageFont.load_default(size=size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
    return draw.textlength(text, font=font)


def _break_long_word(draw: ImageDraw.ImageDraw, word: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """Splits a single word that alone exceeds max_width by characters, so
    no produced line can ever overflow the safe width (task's explicit
    "never allow clipping outside the frame" requirement) even for
    pathological input with no spaces."""
    chunks: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if not current or _text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            chunks.append(current)
            current = char
    if current:
        chunks.append(current)
    return chunks


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """Real word-wrap using actual measured text width -- the capability
    ffmpeg's drawtext filter does not have on its own."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and _text_width(draw, candidate, font) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
            if _text_width(draw, current, font) > max_width and " " not in current:
                broken = _break_long_word(draw, current, font, max_width)
                lines.extend(broken[:-1])
                current = broken[-1]
        if current:
            lines.append(current)
    return lines


def _mark_truncated(draw: ImageDraw.ImageDraw, line: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """Marks `line` as truncated with a trailing ellipsis -- used when
    _fit_text_block() drops one or more wrapped lines because the text
    exceeded max_lines even at the minimum font size. This must ALWAYS
    visibly add the ellipsis, even if `line` itself already fits within
    `max_width` on its own: the line was a perfectly normal wrap point,
    but content after it is being silently dropped, and the task's
    "intelligently shorten or truncate" requirement means that must be
    visible to the viewer, not silent.
    """
    if not line:
        return ELLIPSIS
    with_ellipsis = f"{line} {ELLIPSIS}"
    if _text_width(draw, with_ellipsis, font) <= max_width:
        return with_ellipsis
    while line and _text_width(draw, line + ELLIPSIS, font) > max_width:
        line = line[:-1].rstrip()
    return (line + ELLIPSIS) if line else ELLIPSIS


def _fit_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Optional[str],
    *,
    max_width: float,
    max_lines: int,
    font_size_max: int,
    font_size_min: int,
) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """The real layout engine: wraps `text` to `max_width`, shrinking the
    font size step by step while it still overflows `max_lines`, and
    -- only if it still does not fit at the minimum size -- intelligently
    truncates the last visible line with an ellipsis rather than ever
    letting it (or any other line) run past `max_width`. This is what
    guarantees requirements 2-5 and 8 (wrap, shrink, line caps, no
    clipping) instead of the old single fixed-size, non-wrapping
    `drawtext` call.
    """
    size = font_size_max
    lines: list[str] = []
    font = _load_font(font_path, size)
    while size >= font_size_min:
        font = _load_font(font_path, size)
        lines = _wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return lines, font
        size -= FONT_SIZE_STEP

    # Still does not fit within max_lines even at the minimum size --
    # truncate rather than overflow the frame (task's explicit
    # "intelligently shorten or truncate ... but never allow clipping"
    # requirement).
    font = _load_font(font_path, font_size_min)
    lines = _wrap_text(draw, text, font, max_width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _mark_truncated(draw, lines[-1], font, max_width)
    return lines, font


def _line_height(font: ImageFont.FreeTypeFont) -> float:
    ascent, descent = font.getmetrics()
    return (ascent + descent) * LINE_SPACING_RATIO


def _block_height(lines: list[str], font: ImageFont.FreeTypeFont) -> float:
    return _line_height(font) * max(len(lines), 1)


def _draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    *,
    center_x: float,
    top_y: float,
    fill: tuple[int, int, int, int],
) -> None:
    line_height = _line_height(font)
    y = top_y
    for line in lines:
        width = _text_width(draw, line, font)
        x = center_x - width / 2
        draw.text((x + SHADOW_OFFSET, y + SHADOW_OFFSET), line, font=font, fill=SHADOW_COLOR)
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height


@dataclass(frozen=True)
class _TextLayoutProfile:
    """Parameterizes _render_text_layer() for the two card styles: the
    full, vertically-centered info card, and the compact, top-anchored
    hybrid-scene overlay (see module docstring)."""

    margin_h: int
    margin_v: int
    title_max_lines: int
    body_max_lines: int
    title_font_size_max: int
    title_font_size_min: int
    body_font_size_max: int
    body_font_size_min: int
    vertical_anchor: str  # "center" | "top"


FULL_CARD_PROFILE = _TextLayoutProfile(
    margin_h=MARGIN_H, margin_v=MARGIN_V,
    title_max_lines=TITLE_MAX_LINES, body_max_lines=BODY_MAX_LINES,
    title_font_size_max=TITLE_FONT_SIZE_MAX, title_font_size_min=TITLE_FONT_SIZE_MIN,
    body_font_size_max=BODY_FONT_SIZE_MAX, body_font_size_min=BODY_FONT_SIZE_MIN,
    vertical_anchor="center",
)

HYBRID_OVERLAY_PROFILE = _TextLayoutProfile(
    margin_h=MARGIN_H, margin_v=HYBRID_TOP_MARGIN,
    title_max_lines=HYBRID_TITLE_MAX_LINES, body_max_lines=HYBRID_BODY_MAX_LINES,
    title_font_size_max=HYBRID_TITLE_FONT_SIZE_MAX, title_font_size_min=HYBRID_TITLE_FONT_SIZE_MIN,
    body_font_size_max=HYBRID_BODY_FONT_SIZE_MAX, body_font_size_min=HYBRID_BODY_FONT_SIZE_MIN,
    vertical_anchor="top",
)


def _render_text_layer(
    headline: str, key_fact: str, font_path: Optional[str], profile: _TextLayoutProfile = FULL_CARD_PROFILE
) -> Image.Image:
    """Builds a full-frame (1080x1920) transparent RGBA layer with the
    title and body text laid out inside `profile`'s safe margins, on top
    of a rounded semi-transparent panel for legibility over any background
    -- composited onto the video via ffmpeg's `overlay` filter (see
    `_composite_card()`).
    """
    layer = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    safe_width = CARD_WIDTH - 2 * profile.margin_h

    title_lines, title_font = _fit_text_block(
        draw, headline, font_path,
        max_width=safe_width, max_lines=profile.title_max_lines,
        font_size_max=profile.title_font_size_max, font_size_min=profile.title_font_size_min,
    )
    body_lines: list[str] = []
    body_font = _load_font(font_path, profile.body_font_size_min)
    if key_fact.strip():
        body_lines, body_font = _fit_text_block(
            draw, key_fact, font_path,
            max_width=safe_width, max_lines=profile.body_max_lines,
            font_size_max=profile.body_font_size_max, font_size_min=profile.body_font_size_min,
        )

    title_height = _block_height(title_lines, title_font)
    body_height = _block_height(body_lines, body_font) if body_lines else 0.0
    spacing = BLOCK_SPACING_PX if body_lines else 0.0
    total_height = title_height + spacing + body_height

    safe_top = profile.margin_v
    safe_bottom = CARD_HEIGHT - profile.margin_v
    if profile.vertical_anchor == "top":
        # Anchored near the top (hybrid overlay over real footage) rather
        # than centered in the whole frame -- keeps the overlay compact and
        # out of the way of the footage itself.
        top_y = safe_top
    else:
        # Vertically center the whole title+body stack inside the safe
        # area -- this is what keeps short text looking intentionally
        # placed and long (near max_lines) text still fully inside the
        # top/bottom margins, instead of a single hard-coded y fraction
        # that only worked for one specific amount of text.
        top_y = safe_top + max(0.0, (safe_bottom - safe_top - total_height) / 2)

    center_x = CARD_WIDTH / 2

    # Panel bounds from the actual rendered content width, not the full
    # safe width, so a short headline gets a snug card rather than a
    # full-width bar -- clamped so the panel itself never crosses the
    # frame (independent of, and slightly looser than, the text margin).
    content_width = max(
        [_text_width(draw, line, title_font) for line in title_lines]
        + [_text_width(draw, line, body_font) for line in body_lines]
        + [0.0]
    )
    panel_left = max(PANEL_MIN_MARGIN, center_x - content_width / 2 - PANEL_PADDING_H)
    panel_right = min(CARD_WIDTH - PANEL_MIN_MARGIN, center_x + content_width / 2 + PANEL_PADDING_H)
    panel_top = max(PANEL_MIN_MARGIN, top_y - PANEL_PADDING_V)
    panel_bottom = min(CARD_HEIGHT - PANEL_MIN_MARGIN, top_y + total_height + PANEL_PADDING_V)
    if panel_right > panel_left and panel_bottom > panel_top:
        draw.rounded_rectangle((panel_left, panel_top, panel_right, panel_bottom), radius=PANEL_RADIUS, fill=PANEL_FILL)

    _draw_centered_lines(draw, title_lines, title_font, center_x=center_x, top_y=top_y, fill=TITLE_COLOR)
    if body_lines:
        _draw_centered_lines(
            draw, body_lines, body_font, center_x=center_x, top_y=top_y + title_height + spacing, fill=BODY_COLOR
        )

    return layer


def _render_gradient_background() -> Image.Image:
    """A subtle top-to-bottom dark gradient with two soft blurred "glow"
    accents -- used by render_info_card() when no real photo/video
    background exists, instead of one flat solid color, which read as an
    unfinished placeholder rather than a designed scene (see module
    docstring's "layered background" / "subtle gradient" requirement).
    """
    background = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), GRADIENT_TOP_COLOR)
    draw = ImageDraw.Draw(background)
    height_range = max(CARD_HEIGHT - 1, 1)
    for y in range(CARD_HEIGHT):
        t = y / height_range
        color = tuple(
            int(GRADIENT_TOP_COLOR[channel] + (GRADIENT_BOTTOM_COLOR[channel] - GRADIENT_TOP_COLOR[channel]) * t)
            for channel in range(3)
        )
        draw.line([(0, y), (CARD_WIDTH, y)], fill=color)

    glow_layer = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_draw.ellipse((-250, -250, 550, 550), fill=(*GLOW_COLOR, 70))
    glow_draw.ellipse((CARD_WIDTH - 450, CARD_HEIGHT - 650, CARD_WIDTH + 350, CARD_HEIGHT + 150), fill=(*GLOW_COLOR, 45))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(GLOW_BLUR_RADIUS))

    return Image.alpha_composite(background.convert("RGBA"), glow_layer).convert("RGB")


def _composite_card(
    text_layer: Image.Image,
    dest_path: Path,
    *,
    background_path: Optional[Path],
    background_is_video: bool,
    background_brightness: float,
    background_saturation: float,
    log_label: str,
) -> Path:
    """Shared ffmpeg compositing pipeline for both render_info_card() and
    render_hybrid_scene(). Background is a real photo/video when given,
    darkened by `background_brightness`/`background_saturation` for text
    legibility, else a designed gradient (`_render_gradient_background()`)
    -- never one flat solid color. The text layer gets a deterministic
    fade-in + slide-up (TEXT_FADE_IN_SECONDS/TEXT_SLIDE_UP_PX) via ffmpeg's
    own `fade` filter and a time-varying `overlay` y-expression (no
    per-frame Python rendering). A flat/photo backdrop additionally gets a
    slow `zoompan` zoom; a video backdrop already has its own motion.
    """
    ffmpeg = require_binary("ffmpeg")
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    apply_zoom = True
    gradient_bg_path: Optional[Path] = None
    if background_path is not None and Path(background_path).exists():
        if background_is_video:
            input_args = ["-stream_loop", "-1", "-t", str(CARD_DURATION_SECONDS), "-i", str(background_path)]
            apply_zoom = False  # the backdrop's own motion is enough
        else:
            input_args = ["-loop", "1", "-t", str(CARD_DURATION_SECONDS), "-i", str(background_path)]
        backdrop_filter = (
            f"scale={CARD_WIDTH}:{CARD_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={CARD_WIDTH}:{CARD_HEIGHT},eq=brightness={background_brightness}:saturation={background_saturation}"
        )
    else:
        with tempfile.NamedTemporaryFile(dir=dest_path.parent, prefix=f".{dest_path.stem}_bg_", suffix=".png", delete=False) as bg_file:
            gradient_bg_path = Path(bg_file.name)
        _render_gradient_background().save(gradient_bg_path)
        input_args = ["-loop", "1", "-t", str(CARD_DURATION_SECONDS), "-i", str(gradient_bg_path)]
        backdrop_filter = "null"

    with tempfile.NamedTemporaryFile(dir=dest_path.parent, prefix=f".{dest_path.stem}_text_", suffix=".png", delete=False) as tmp_file:
        text_layer_path = Path(tmp_file.name)
    text_layer.save(text_layer_path)

    try:
        # IMPORTANT: zoompan must be applied to the plain background ONLY,
        # strictly BEFORE the text overlay is composited on top -- not to
        # the already-composited [stacked] stream. zoompan treats its
        # input as a single sampled image it progressively zooms into
        # across `d` frames; fed a stream that is itself animating
        # (the fading/sliding text), it effectively freezes on that
        # stream's very first (pre-fade, still-transparent) frame for the
        # whole duration, silently discarding the text animation entirely.
        # This was caught by manually inspecting a rendered frame, not by
        # the automated tests (which only check ffmpeg's exit code and
        # output resolution, not pixel content).
        if apply_zoom:
            frames = int(CARD_DURATION_SECONDS * CARD_FPS)
            bg_chain = (
                f"[0:v]{backdrop_filter},"
                f"zoompan=z='min(zoom+0.0006,1.1)':d={frames}:s={CARD_WIDTH}x{CARD_HEIGHT}:fps={CARD_FPS}[bg]"
            )
        else:
            bg_chain = f"[0:v]{backdrop_filter}[bg]"

        text_filter = f"format=rgba,fade=t=in:st=0:d={TEXT_FADE_IN_SECONDS}:alpha=1"
        slide_expr = f"if(lt(t,{TEXT_FADE_IN_SECONDS}),{TEXT_SLIDE_UP_PX}*(1-t/{TEXT_FADE_IN_SECONDS}),0)"
        chain = (
            f"{bg_chain};[1:v]{text_filter}[text];"
            f"[bg][text]overlay=x=0:y='{slide_expr}'[stacked];"
            "[stacked]format=yuv420p[out]"
        )

        cmd = [
            ffmpeg, "-y",
            *input_args,
            "-loop", "1", "-i", str(text_layer_path),
            "-filter_complex", chain,
            "-map", "[out]",
            "-an",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", str(CARD_FPS),
            "-t", str(CARD_DURATION_SECONDS),
            str(dest_path),
        ]

        logger.info("Rendering %s -> %s", log_label, dest_path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise InfoCardError(f"ffmpeg failed rendering {log_label} (exit {result.returncode}): {result.stderr[-4000:]}")
    finally:
        text_layer_path.unlink(missing_ok=True)
        if gradient_bg_path is not None:
            gradient_bg_path.unlink(missing_ok=True)

    return dest_path


def render_info_card(
    headline: str,
    key_fact: str,
    dest_path: Path,
    background_path: Optional[Path] = None,
    background_is_video: bool = False,
) -> Path:
    """Render a designed, motion-enhanced text information card -- a
    subtle gradient/tech background when no real photo/video background is
    given, else that real backdrop darkened for legibility (see
    docs/PRODUCTION_PIPELINE.md "Visual production modes").

    headline and key_fact are truncated defensively -- this is a short
    caption card (headline + at most one short supporting line), not a
    full paragraph (see the task's explicit "prefer headline + one short
    supporting statement instead of paragraph-style text" requirement).
    The real per-frame layout (wrapping, line caps, font-size reduction,
    safe margins) happens in `_render_text_layer()`; the actual ffmpeg
    background/motion/encoding pipeline is shared with
    `render_hybrid_scene()` via `_composite_card()`.
    """
    headline_text = headline.strip()[:HEADLINE_MAX_CHARS] or "Worth knowing"
    fact_text = key_fact.strip()[:FACT_MAX_CHARS]

    font_file = _find_font_file()
    if font_file is None:
        logger.warning("No system font file found among %s -- falling back to Pillow's built-in font", _FONT_FILE_CANDIDATES)

    text_layer = _render_text_layer(headline_text, fact_text, font_file, profile=FULL_CARD_PROFILE)

    return _composite_card(
        text_layer, Path(dest_path),
        background_path=background_path, background_is_video=background_is_video,
        background_brightness=-0.28, background_saturation=0.55,
        log_label=f"information card (headline={headline_text!r})",
    )


def render_hybrid_scene(
    background_path: Path,
    background_is_video: bool,
    headline: str,
    key_fact: str,
    dest_path: Path,
) -> Path:
    """Renders a "hybrid_visual" scene: a REAL, already Vision-approved
    photo/video background (see asset_acquisition.py) plus a small,
    top-anchored honest overlay card -- the task's example: "MYTH 2" /
    "You need a flagship GPU" over gaming-PC-setup footage, instead of a
    full-screen text block. Used when the best available visual is
    genuinely relevant/honest but not a strong or exact match on its own
    (see vision_validation.py's EXACT_MATCH_SCORE/RESCUE_MIN_SCORE).

    Unlike render_info_card(), `background_path` is REQUIRED -- there is
    no flat/gradient fallback here; a hybrid scene with no real background
    would just be an info card (see asset_acquisition.py's mode-selection
    logic, which never calls this function without one). The background is
    only lightly dimmed (unlike the full info-card backdrop) so the real
    footage still reads as the primary visual, with the overlay adding
    context rather than dominating the frame.
    """
    background_path = Path(background_path)
    if not background_path.exists():
        raise InfoCardError(f"Hybrid scene background does not exist: {background_path}")

    headline_text = headline.strip()[:HEADLINE_MAX_CHARS] or "Worth knowing"
    fact_text = key_fact.strip()[:FACT_MAX_CHARS]

    font_file = _find_font_file()
    if font_file is None:
        logger.warning("No system font file found among %s -- falling back to Pillow's built-in font", _FONT_FILE_CANDIDATES)

    text_layer = _render_text_layer(headline_text, fact_text, font_file, profile=HYBRID_OVERLAY_PROFILE)

    return _composite_card(
        text_layer, Path(dest_path),
        background_path=background_path, background_is_video=background_is_video,
        background_brightness=-0.10, background_saturation=0.85,
        log_label=f"hybrid scene (headline={headline_text!r})",
    )
