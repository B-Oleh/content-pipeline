"""Builds short, readable, synchronized subtitle cues from TTS word timings
and writes them as an .ass (SubStation Alpha) file for ffmpeg to burn in.

Word-level timing comes from providers/voice.py; this module groups words
into caption-sized chunks, wraps each chunk to at most MAX_LINES_PER_CUE
lines, and writes the .ass file -- no TTS/network logic lives here, so it
is directly unit-testable with plain WordTiming lists.

Why .ass instead of .srt: ffmpeg's `subtitles` filter converts a plain .srt
to ASS internally before rendering, and that automatic conversion applies
force_style pixel values (FontSize, margins) against some internal
reference resolution that does NOT reliably match the real output frame --
even explicitly passing the filter's own `original_size` option did not
correct it in testing. The practical effect was captions rendered several
times larger than requested ("subtitles are too large" bug). Writing our
own .ass file with an explicit `PlayResX`/`PlayResY` header equal to the
real output resolution removes the ambiguity entirely: every Style pixel
value (Fontsize, MarginL/R/V) is then applied 1:1 against real frame
pixels, verified empirically against the actual installed ffmpeg/libass
build. All styling constants live here, in one place, since
video_assembly.py no longer needs to know anything about subtitle style --
it only points ffmpeg at the .ass file this module writes.
"""

from __future__ import annotations

from pathlib import Path

from scripts.production.models import SubtitleCue
from scripts.production.providers.voice import WordTiming
from scripts.utils.atomic_write import atomic_write_text

MAX_WORDS_PER_CUE = 6
MAX_SECONDS_PER_CUE = 3.0
# Only true sentence-enders force a break; a comma is a natural micro-pause,
# not a caption boundary -- breaking on every comma produced awkward
# one-or-two-word captions instead of short-but-readable ones.
_SENTENCE_END_CHARS = (".", "!", "?")

# --- Burned-in subtitle layout for a 1080x1920 vertical frame ---------------
FRAME_WIDTH_PX = 1080
FRAME_HEIGHT_PX = 1920
FONT_NAME = "Arial"  # fontconfig substitutes a similar sans-serif if unavailable
FONT_SIZE_PX = 58  # within the requested 54-64px range
HORIZONTAL_MARGIN_PX = 100  # ~100px safe margin on each side
BOTTOM_MARGIN_PX = 200  # within the requested 180-220px range, lower third
MAX_LINES_PER_CUE = 2
_PRIMARY_COLOUR = "&H00FFFFFF"  # opaque white text
_OUTLINE_COLOUR = "&H00000000"  # opaque black outline/shadow
_OUTLINE_WIDTH = 3
_SHADOW_DEPTH = 1
_ALIGNMENT_BOTTOM_CENTER = 2  # ASS numpad alignment: lower third, centered

# There is no real font-metrics dependency in this pipeline, so this is a
# conservative average-glyph-width heuristic (as a fraction of font size in
# px) used only to decide where a manual line break belongs -- not for
# pixel-perfect layout. Deliberately generous (wraps a bit earlier than a
# narrower font would need) so lines stay comfortably inside the frame.
_AVG_CHAR_WIDTH_RATIO = 0.56

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_colour},&H000000FF,{outline_colour},&H00000000,1,0,0,0,100,100,0,0,1,{outline},{shadow},{alignment},{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _max_chars_per_line(
    frame_width_px: int = FRAME_WIDTH_PX,
    horizontal_margin_px: int = HORIZONTAL_MARGIN_PX,
    font_size_px: int = FONT_SIZE_PX,
) -> int:
    usable_width_px = frame_width_px - 2 * horizontal_margin_px
    return max(int(usable_width_px / (font_size_px * _AVG_CHAR_WIDTH_RATIO)), 1)


def wrap_cue_text(text: str, *, max_chars_per_line: int | None = None, max_lines: int = MAX_LINES_PER_CUE) -> str:
    """Wrap text into at most max_lines lines, breaking at word boundaries.

    Guarantees at most max_lines lines by construction: once max_lines - 1
    lines have been flushed, every remaining word is appended to the final
    line regardless of width, rather than starting a third line. In
    practice MAX_WORDS_PER_CUE keeps chunks short enough that this never
    needs to trigger.
    """
    if max_chars_per_line is None:
        max_chars_per_line = _max_chars_per_line()

    words = text.split()
    if not words:
        return text

    lines: list[str] = []
    current_line_words: list[str] = []
    for word in words:
        candidate = " ".join([*current_line_words, word])
        can_start_new_line = len(lines) < max_lines - 1
        if current_line_words and len(candidate) > max_chars_per_line and can_start_new_line:
            lines.append(" ".join(current_line_words))
            current_line_words = [word]
        else:
            current_line_words.append(word)
    lines.append(" ".join(current_line_words))

    return "\n".join(lines[:max_lines])


def group_words_into_cues(
    words: list[WordTiming],
    *,
    time_offset: float = 0.0,
    start_index: int = 1,
    max_words: int = MAX_WORDS_PER_CUE,
    max_seconds: float = MAX_SECONDS_PER_CUE,
) -> list[SubtitleCue]:
    """Group consecutive words into short caption chunks.

    time_offset shifts every cue onto a larger (e.g. full-video) timeline --
    each scene's narration is synthesized separately (see voice_generation.py),
    so this is how per-scene word timings become global subtitle timing.
    """
    cues: list[SubtitleCue] = []
    current: list[WordTiming] = []

    def flush() -> None:
        if not current:
            return
        text = wrap_cue_text(" ".join(word.text for word in current))
        cues.append(
            SubtitleCue(
                index=start_index + len(cues),
                start=time_offset + current[0].start,
                end=time_offset + current[-1].end,
                text=text,
            )
        )

    for word in words:
        # Adding this word first, then checking, could overshoot max_seconds
        # by up to one word's duration -- check before adding instead, so a
        # cue's span never exceeds max_seconds (unless a single word alone
        # already would, in which case it must go out on its own).
        would_overflow_duration = current and (word.end - current[0].start) > max_seconds
        if current and (len(current) >= max_words or would_overflow_duration):
            flush()
            current = []

        current.append(word)
        ends_sentence = word.text.rstrip().endswith(_SENTENCE_END_CHARS)
        if ends_sentence:
            flush()
            current = []
    flush()

    return cues


def _format_ass_timestamp(seconds: float) -> str:
    """ASS timestamps are H:MM:SS.CC (centiseconds), hours not zero-padded."""
    seconds = max(seconds, 0.0)
    centiseconds_total = round(seconds * 100)
    hours, remainder = divmod(centiseconds_total, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _escape_ass_text(text: str) -> str:
    # Curly braces trigger ASS override tags -- strip them so spoken-text
    # content can never accidentally inject markup.
    text = text.replace("{", "").replace("}", "")
    # wrap_cue_text() joins lines with a real newline; ASS instead requires
    # the literal two-character escape \N, since each Dialogue event must
    # be a single physical line in the .ass file.
    return text.replace("\n", "\\N")


def write_ass(cues: list[SubtitleCue], path: Path) -> None:
    """Write cues as an .ass file (atomic write, UTF-8) -- see module
    docstring for why .ass rather than .srt."""
    header = _ASS_HEADER.format(
        width=FRAME_WIDTH_PX,
        height=FRAME_HEIGHT_PX,
        font_name=FONT_NAME,
        font_size=FONT_SIZE_PX,
        primary_colour=_PRIMARY_COLOUR,
        outline_colour=_OUTLINE_COLOUR,
        outline=_OUTLINE_WIDTH,
        shadow=_SHADOW_DEPTH,
        alignment=_ALIGNMENT_BOTTOM_CENTER,
        margin_l=HORIZONTAL_MARGIN_PX,
        margin_r=HORIZONTAL_MARGIN_PX,
        margin_v=BOTTOM_MARGIN_PX,
    )
    event_lines = [
        f"Dialogue: 0,{_format_ass_timestamp(cue.start)},{_format_ass_timestamp(cue.end)},Default,,0,0,0,,{_escape_ass_text(cue.text)}"
        for cue in cues
    ]
    atomic_write_text(path, header + "\n".join(event_lines) + "\n")
