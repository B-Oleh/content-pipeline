"""Voice/TTS provider boundary. First working pipeline uses edge-tts (free,
no API key) -- see CLAUDE.md "Provider abstraction" and the task's explicit
"No paid TTS" requirement for this milestone.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WordTiming:
    """One spoken word and when it occurs, in seconds from the start of
    this synthesis call's audio."""

    text: str
    start: float
    end: float


class VoiceProvider(ABC):
    """A source of narration audio plus word-level timing."""

    @abstractmethod
    def synthesize(self, text: str, dest_audio_path: Path) -> list[WordTiming]:
        """Write narration audio to dest_audio_path; return word timings."""
        raise NotImplementedError


class EdgeTtsProvider(VoiceProvider):
    """Microsoft Edge's free text-to-speech, via the edge-tts package."""

    def __init__(self, voice: str = "en-US-AriaNeural") -> None:
        self._voice = voice

    def synthesize(self, text: str, dest_audio_path: Path) -> list[WordTiming]:
        return asyncio.run(self._synthesize_async(text, dest_audio_path))

    async def _synthesize_async(self, text: str, dest_audio_path: Path) -> list[WordTiming]:
        import edge_tts

        dest_audio_path.parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(text, self._voice, boundary="WordBoundary")
        word_timings: list[WordTiming] = []
        with open(dest_audio_path, "wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # offset/duration are in 100-nanosecond units.
                    start = chunk["offset"] / 10_000_000
                    end = (chunk["offset"] + chunk["duration"]) / 10_000_000
                    word_timings.append(WordTiming(text=chunk["text"], start=start, end=end))
        return word_timings
