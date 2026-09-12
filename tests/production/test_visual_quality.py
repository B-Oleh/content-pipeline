"""Gate tests also runnable with the standard library when pytest is unavailable."""
import tempfile
import unittest
from pathlib import Path

from scripts.production.models import Scene
from scripts.production.visual_quality import check_visual_quality


class VisualQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.asset = Path(self.temp.name) / "accepted.jpg"
        self.asset.write_bytes(b"representative acquired media")

    def scene(self, duration, mode="real_visual", **kwargs):
        defaults = dict(asset_source="pexels", media_accepted=True, asset_path=self.asset)
        defaults.update(kwargs)
        return Scene(0, "PC hardware", duration_seconds=duration, production_mode=mode, **defaults)

    def test_exact_boundary_and_photo_are_accepted(self):
        scenes = [self.scene(8, asset_is_video=False), self.scene(2, "info_card")]
        self.assertTrue(check_visual_quality(scenes).passed)

    def test_duration_not_scene_count(self):
        scenes = [self.scene(1) for _ in range(9)] + [self.scene(9, "info_card")]
        self.assertFalse(check_visual_quality(scenes).checks["real_media_coverage"])

    def test_two_cards_fail_even_with_high_coverage(self):
        scenes = [self.scene(98), self.scene(1, "info_card"), self.scene(1, "info_card")]
        self.assertFalse(check_visual_quality(scenes).passed)

    def test_generated_rejected_missing_and_unknown_media_do_not_count(self):
        for kwargs in [dict(asset_source="generated"), dict(media_accepted=False),
                       dict(asset_path=None), dict(asset_source="other")]:
            with self.subTest(kwargs=kwargs):
                self.assertFalse(check_visual_quality([self.scene(10, **kwargs)]).passed)
        self.assertFalse(check_visual_quality([self.scene(10, "info_card")]).passed)
        self.asset.write_bytes(b"")
        self.assertFalse(check_visual_quality([self.scene(10)]).passed)

    def test_approved_hybrid_counts(self):
        self.assertTrue(check_visual_quality([self.scene(10, "hybrid_visual")]).passed)

    def test_invalid_durations_fail_closed(self):
        for duration in [None, 0, -1, float("nan"), float("inf")]:
            with self.subTest(duration=duration):
                self.assertFalse(check_visual_quality([self.scene(duration)]).passed)
        self.assertFalse(check_visual_quality([]).passed)


if __name__ == "__main__":
    unittest.main()
