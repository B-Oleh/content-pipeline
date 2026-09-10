"""Voice Generation: synthesizes narration per scene and builds global
subtitle cues from the resulting word timings.

See CLAUDE.md pipeline stage 7. Each scene is synthesized separately (rather
than one call for the whole narration) so that scene.duration_seconds --
which drives how long its visual plays in video_assembly.py -- comes
directly from that scene's own audio, keeping video and audio perfectly in
sync with no separate alignment step and no silent gaps: the narration
track covers the entire runtime by construction.
"""

from __future__ import annotations

from pathlib import Path

from scripts.production.ffmpeg_utils import probe_duration_seconds
from scripts.production.models import Scene, SubtitleCue
from scripts.production.providers.voice import VoiceProvider
from scripts.production.subtitles import group_words_into_cues
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)


class VoiceGenerationError(RuntimeError):
    """Raised when a scene's narration could not be synthesized at all."""


def generate_narration(scenes: list[Scene], provider: VoiceProvider, workdir: Path) -> list[SubtitleCue]:
    """Synthesize each scene's audio, set its duration, and return global subtitle cues.

    Mutates each Scene in place (audio_path, duration_seconds).
    """
    audio_dir = Path(workdir) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    all_cues: list[SubtitleCue] = []
    time_offset = 0.0

    for scene in scenes:
        if not scene.narration_line:
            raise VoiceGenerationError(f"Scene {scene.index} has no narration text to synthesize")

        audio_path = audio_dir / f"scene_{scene.index:02d}.mp3"
        try:
            word_timings = provider.synthesize(scene.narration_line, audio_path)
        except Exception as exc:  # noqa: BLE001 -- surfaced as a clear stage failure, not swallowed
            raise VoiceGenerationError(f"Narration synthesis failed for scene {scene.index}: {exc}") from exc

        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise VoiceGenerationError(f"Narration synthesis produced no audio for scene {scene.index}")

        scene.audio_path = audio_path
        # Measured from the actual audio file (not the last word boundary)
        # so video_assembly.py's per-scene video trim length exactly matches
        # this audio's length -- avoiding any video/audio drift across
        # scenes in the concatenated output.
        try:
            scene.duration_seconds = probe_duration_seconds(audio_path)
        except Exception:  # noqa: BLE001 -- fall back rather than fail the whole run over a probe hiccup
            scene.duration_seconds = word_timings[-1].end if word_timings else _estimate_duration(scene.narration_line)

        cues = group_words_into_cues(word_timings, time_offset=time_offset, start_index=len(all_cues) + 1)
        all_cues.extend(cues)

        logger.info("Scene %d: narration %.2fs, %d subtitle cue(s)", scene.index, scene.duration_seconds, len(cues))
        time_offset += scene.duration_seconds

    return all_cues


def _estimate_duration(text: str, words_per_minute: float = 155.0) -> float:
    """Fallback if a provider yields no word timings (e.g. very short text)."""
    word_count = max(len(text.split()), 1)
    return max((word_count / words_per_minute) * 60.0, 1.0)
