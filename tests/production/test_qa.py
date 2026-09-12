"""QA edge cases that don't need a real ffmpeg render (see
test_video_assembly_integration.py for the real-render happy path)."""

from scripts.production.qa import run_qa


def test_missing_file_fails_immediately(tmp_path):
    result = run_qa(
        tmp_path / "does_not_exist.mp4",
        rendered_scene_count=4,
        narration_generated=True,
        subtitles_generated=True,
    )
    assert result.passed is False
    assert result.checks["file_exists"] is False


def test_empty_file_fails(tmp_path):
    path = tmp_path / "empty.mp4"
    path.write_bytes(b"")
    result = run_qa(path, rendered_scene_count=4, narration_generated=True, subtitles_generated=True)
    assert result.passed is False
    assert result.checks["non_zero_size"] is False


def test_wrong_resolution_fails(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"not a real mp4 but non-empty")

    def fake_probe_streams(video_path):
        return {
            "streams": [
                {"codec_type": "video", "width": 640, "height": 480},
                {"codec_type": "audio"},
            ],
            "format": {"duration": "30.0"},
        }

    monkeypatch.setattr("scripts.production.qa.probe_streams", fake_probe_streams)
    result = run_qa(path, rendered_scene_count=4, narration_generated=True, subtitles_generated=True)

    assert result.passed is False
    assert result.checks["resolution_1080x1920"] is False
    assert result.checks["duration_in_range"] is True


def test_too_few_scenes_fails(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"non-empty")

    def fake_probe_streams(video_path):
        return {
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}],
            "format": {"duration": "30.0"},
        }

    monkeypatch.setattr("scripts.production.qa.probe_streams", fake_probe_streams)
    result = run_qa(path, rendered_scene_count=1, narration_generated=True, subtitles_generated=True)

    assert result.passed is False
    assert result.checks["required_scenes_rendered"] is False


def test_missing_narration_or_subtitles_fails(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"non-empty")

    def fake_probe_streams(video_path):
        return {
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}],
            "format": {"duration": "30.0"},
        }

    monkeypatch.setattr("scripts.production.qa.probe_streams", fake_probe_streams)
    result = run_qa(path, rendered_scene_count=4, narration_generated=False, subtitles_generated=False)

    assert result.passed is False
    assert result.checks["narration_generated"] is False
    assert result.checks["subtitles_generated"] is False


def test_ffprobe_failure_is_reported_not_raised(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"non-empty")

    def fake_probe_streams(video_path):
        raise RuntimeError("ffprobe crashed")

    monkeypatch.setattr("scripts.production.qa.probe_streams", fake_probe_streams)
    result = run_qa(path, rendered_scene_count=4, narration_generated=True, subtitles_generated=True)

    assert result.passed is False
    assert result.checks["ffprobe_readable"] is False


def test_qa_rechecks_real_media_and_rendered_timeline(tmp_path, monkeypatch):
    from scripts.production.models import Scene
    path = tmp_path / "video.mp4"
    path.write_bytes(b"representative rendered output")
    info = {
        "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}],
        "format": {"duration": "30.0"},
    }
    monkeypatch.setattr("scripts.production.qa.probe_streams", lambda _: info)
    scenes = [Scene(i, "PC hardware", asset_path=path, asset_source="pexels",
                    media_accepted=True, production_mode="real_visual", duration_seconds=10)
              for i in range(3)]
    kwargs = dict(rendered_scene_count=3, narration_generated=True, subtitles_generated=True, scenes=scenes)
    assert run_qa(path, **kwargs).passed
    scenes[1].media_accepted = False
    assert not run_qa(path, **kwargs).checks["real_media_coverage"]
    scenes[1].media_accepted = True
    info["format"]["duration"] = "35.0"
    assert not run_qa(path, **kwargs).checks["scene_timeline_matches_render"]
