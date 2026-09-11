"""Renders a designed "information card" video clip for scenes where no
honestly-matching stock asset exists (see docs/PRODUCTION_PIPELINE.md
"Visual relevance" and the task's explicit "never imply generic stock
footage is footage of a specific named game, GPU, laptop, or product"
requirement).

Produces an ordinary .mp4 file, so it plugs into asset_acquisition.py and
video_assembly.py exactly like a downloaded stock video: video_assembly.py
already loops/trims any video asset to the scene's real narration duration
(-stream_loop -1 -t <duration>), so this card's own rendered duration only
needs to be long enough to loop smoothly, not match the scene exactly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

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
CARD_BACKGROUND_COLOR = "0x14141f"
HEADLINE_MAX_CHARS = 70
FACT_MAX_CHARS = 110

# ffmpeg's drawtext filter (unlike the `subtitles`/libass filter) needs
# fontconfig to be fully configured to resolve a font *by name* -- on a
# machine where fontconfig's own config file is missing (observed locally
# on Windows: "Fontconfig error: Cannot load default config file"),
# drawtext fails outright with no font specified. Passing an explicit
# `fontfile=` bypasses fontconfig entirely (loaded directly via
# libfreetype), so this looks for a real font file at a handful of common
# install locations instead of assuming fontconfig works. The GitHub
# Actions workflow also explicitly installs a Linux font package alongside
# ffmpeg so one of the Linux paths below is always present there -- see
# produce_video.yml.
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


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg's drawtext `text=`/`fontfile=` parameters."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")  # typographic apostrophe -- avoids unescapable literal quotes
    text = text.replace("%", "\\%")
    return text


def render_info_card(
    headline: str,
    key_fact: str,
    dest_path: Path,
    background_path: Optional[Path] = None,
    background_is_video: bool = False,
) -> Path:
    """Render a text information card, optionally over a darkened backdrop.

    headline and key_fact are truncated defensively -- this is a short
    caption card, not a full paragraph (see the task's example spec:
    "short headline, game/product name, key factual point, subtle
    motion/zoom, contextual background asset").
    """
    ffmpeg = require_binary("ffmpeg")
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    headline_text = _escape_drawtext(headline.strip()[:HEADLINE_MAX_CHARS] or "Worth knowing")
    fact_text = _escape_drawtext(key_fact.strip()[:FACT_MAX_CHARS])

    font_file = _find_font_file()
    if font_file is None:
        logger.warning("No font file found among %s -- relying on fontconfig, which may fail", _FONT_FILE_CANDIDATES)
    font_arg = f"fontfile='{_escape_drawtext(font_file)}':" if font_file else ""

    apply_zoom = True
    if background_path is not None and Path(background_path).exists():
        if background_is_video:
            input_args = ["-stream_loop", "-1", "-t", str(CARD_DURATION_SECONDS), "-i", str(background_path)]
            apply_zoom = False  # the backdrop's own motion is enough
        else:
            input_args = ["-loop", "1", "-t", str(CARD_DURATION_SECONDS), "-i", str(background_path)]
        backdrop_filter = (
            f"scale={CARD_WIDTH}:{CARD_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={CARD_WIDTH}:{CARD_HEIGHT},eq=brightness=-0.28:saturation=0.55"
        )
    else:
        input_args = ["-f", "lavfi", "-i", f"color=c={CARD_BACKGROUND_COLOR}:s={CARD_WIDTH}x{CARD_HEIGHT}:d={CARD_DURATION_SECONDS}"]
        backdrop_filter = "null"

    drawtext = (
        f"drawtext={font_arg}text='{headline_text}':fontsize=64:fontcolor=white:"
        "x=(w-text_w)/2:y=h*0.36:box=1:boxcolor=black@0.5:boxborderw=24"
    )
    if fact_text:
        drawtext += (
            f",drawtext={font_arg}text='{fact_text}':fontsize=42:fontcolor=white:"
            "x=(w-text_w)/2:y=h*0.5:box=1:boxcolor=black@0.4:boxborderw=18"
        )

    chain = f"[0:v]{backdrop_filter},{drawtext}"
    if apply_zoom:
        frames = int(CARD_DURATION_SECONDS * CARD_FPS)
        chain += f",zoompan=z='min(zoom+0.0006,1.1)':d={frames}:s={CARD_WIDTH}x{CARD_HEIGHT}:fps={CARD_FPS}"
    chain += ",format=yuv420p[out]"

    cmd = [
        ffmpeg, "-y", *input_args,
        "-filter_complex", chain,
        "-map", "[out]",
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(CARD_FPS),
        "-t", str(CARD_DURATION_SECONDS),
        str(dest_path),
    ]

    logger.info("Rendering information card (headline=%r) -> %s", headline_text, dest_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise InfoCardError(f"ffmpeg failed rendering info card (exit {result.returncode}): {result.stderr[-4000:]}")
    return dest_path
