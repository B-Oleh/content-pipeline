"""Data models for the video production pipeline.

Mirrors scripts/research/models.py's style: plain dataclasses with
to_dict()/from_dict() where persistence/debugging needs it. These are
transient, per-run objects (see docs/PRODUCTION_PIPELINE.md) -- nothing here
is persisted as pipeline state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# A scene's visual production mode -- recorded so it is possible to audit,
# from script.json alone, how "visually alive" a produced video actually
# is (see docs/PRODUCTION_PIPELINE.md "Visual production modes"). Preferred
# in this order: a rejected/unvalidated asset is never used in any mode.
PRODUCTION_MODE_REAL_VISUAL = "real_visual"  # a strong (near-exact), Vision-approved match, used as-is
PRODUCTION_MODE_HYBRID_VISUAL = "hybrid_visual"  # a genuinely relevant but not exact/strong match, real
#   footage + a small honest headline/overlay card to add context
PRODUCTION_MODE_INFO_CARD = "info_card"  # no honest real/contextual visual found -- a designed text card


@dataclass
class Scene:
    """One shot in the video: a narration line plus its visual treatment."""

    index: int
    narration_line: str
    on_screen_text: Optional[str] = None
    visual_search_queries: list[str] = field(default_factory=list)

    # Filled in by asset_acquisition.py
    asset_path: Optional[Path] = None
    asset_is_video: bool = True
    asset_source: Optional[str] = None  # "pexels" | "pixabay" | "info_card"
    asset_url: Optional[str] = None
    asset_attribution: Optional[str] = None
    # One of PRODUCTION_MODE_REAL_VISUAL / _HYBRID_VISUAL / _INFO_CARD (see
    # above) -- None until asset_acquisition.py has processed this scene.
    production_mode: Optional[str] = None

    # Filled in by voice_generation.py -- this scene's own narration audio
    # duration drives how long its visual plays (see video_assembly.py),
    # which is what keeps video and audio in sync with no silent gaps.
    audio_path: Optional[Path] = None
    duration_seconds: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "narration_line": self.narration_line,
            "on_screen_text": self.on_screen_text,
            "visual_search_queries": self.visual_search_queries,
            "asset_path": str(self.asset_path) if self.asset_path else None,
            "asset_is_video": self.asset_is_video,
            "asset_source": self.asset_source,
            "asset_url": self.asset_url,
            "asset_attribution": self.asset_attribution,
            "production_mode": self.production_mode,
            "audio_path": str(self.audio_path) if self.audio_path else None,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class VideoScript:
    """Structured output of Script Agent (see providers/llm.py)."""

    topic: str
    content_role: str
    hook: str
    narration: str
    scenes: list[Scene]
    title: str
    description: str
    evidence_references: list[str] = field(default_factory=list)
    candidate_id: Optional[str] = None
    overall_score: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "content_role": self.content_role,
            "hook": self.hook,
            "narration": self.narration,
            "scenes": [scene.to_dict() for scene in self.scenes],
            "title": self.title,
            "description": self.description,
            "evidence_references": self.evidence_references,
            "candidate_id": self.candidate_id,
            "overall_score": self.overall_score,
        }


@dataclass
class SubtitleCue:
    """One burned-in caption chunk, timed against the final narration track."""

    index: int
    start: float  # seconds from the start of the full video
    end: float
    text: str


@dataclass
class QAResult:
    """Deterministic pre-delivery checks (see production/qa.py)."""

    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checks": self.checks, "details": self.details}

    def summary_lines(self) -> list[str]:
        lines = []
        for name, ok in self.checks.items():
            marker = "OK" if ok else "FAILED"
            detail = self.details.get(name, "")
            lines.append(f"[{marker}] {name}" + (f" -- {detail}" if detail else ""))
        return lines
