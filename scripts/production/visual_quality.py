"""Duration-based production gate using the assets actually passed to rendering."""

from math import isfinite
from pathlib import Path

from scripts.production.models import Scene, QAResult


class VisualQualityError(RuntimeError):
    """No remaining topic can meet the visual production requirements."""


def media_coverage_fraction(scenes: list[Scene]) -> float:
    """Return duration-weighted coverage by accepted, non-empty real media."""
    total = real = 0.0
    for scene in scenes:
        duration = scene.duration_seconds
        if duration is None or not isfinite(duration) or duration <= 0:
            continue
        total += duration
        path = Path(scene.asset_path) if scene.asset_path else None
        external = (
            scene.asset_source in {"pexels", "pixabay"}
            and scene.production_mode in {"real_visual", "hybrid_visual"}
            and scene.media_accepted
            and path is not None and path.is_file() and path.stat().st_size > 0
        )
        if external:
            real += duration
    return real / total if total else 0.0


def check_visual_quality(scenes: list[Scene]) -> QAResult:
    total = 0.0
    streak = longest = 0
    valid = bool(scenes)
    for scene in scenes:
        duration = scene.duration_seconds
        if duration is None or not isfinite(duration) or duration <= 0:
            valid = False
            continue
        total += duration
        streak = streak + 1 if scene.production_mode == "info_card" else 0
        longest = max(longest, streak)
    coverage = media_coverage_fraction(scenes)
    real = coverage * total
    checks = {
        "valid_scene_durations": valid,
        "real_media_coverage": valid and coverage >= 0.8 - 1e-9,
        "info_card_streak": longest <= 1,
    }
    return QAResult(all(checks.values()), checks, {
        "real_media_coverage": f"{real:.3f}/{total:.3f}s ({coverage:.1%}); required 80%",
        "info_card_streak": f"{longest} consecutive; maximum 1",
    })
