"""models.py tests -- Scene.production_mode is a persisted, first-class
field (see asset_acquisition.py), not just an in-memory attribute, so it
must round-trip through to_dict() for auditing from script.json alone."""

from __future__ import annotations

from scripts.production.models import (
    PRODUCTION_MODE_HYBRID_VISUAL,
    PRODUCTION_MODE_INFO_CARD,
    PRODUCTION_MODE_REAL_VISUAL,
    Scene,
)


def test_production_mode_defaults_to_none_before_asset_acquisition_runs():
    scene = Scene(index=0, narration_line="Some narration")
    assert scene.production_mode is None
    assert scene.to_dict()["production_mode"] is None


def test_production_mode_is_recorded_in_to_dict_for_each_mode():
    for mode in (PRODUCTION_MODE_REAL_VISUAL, PRODUCTION_MODE_HYBRID_VISUAL, PRODUCTION_MODE_INFO_CARD):
        scene = Scene(index=0, narration_line="Some narration", production_mode=mode)
        assert scene.to_dict()["production_mode"] == mode
