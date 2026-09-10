"""Builds short, readable, synchronized subtitle cues from TTS word timings.

Word-level timing comes from providers/voice.py; this module only groups
words into caption-sized chunks and writes an .srt file -- no TTS/network
logic lives here, so it is directly unit-testable with plain WordTiming
lists.
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
        text = " ".join(word.text for word in current)
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


def _format_timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def write_srt(cues: list[SubtitleCue], path: Path) -> None:
    """Write cues as a standard .srt file (atomic write, UTF-8)."""
    blocks = []
    for cue in cues:
        blocks.append(f"{cue.index}\n{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}\n{cue.text}\n")
    atomic_write_text(path, "\n".join(blocks))
