"""Batch orchestration exercises real selection/gates/delivery with offline providers."""
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests

from scripts.production import batch
from scripts.production.content_brief import ContentBrief
from scripts.production.models import QAResult, Scene, SubtitleCue, VideoScript
from scripts.research.models import (
    ContentPillar, ContentRole, ResearchCandidate, ResearchResult,
    ScoreBreakdown, ScoredCandidate, SCORE_DIMENSIONS,
)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    topics = [ScoredCandidate(
        ResearchCandidate(str(i), f"PC cooling option {i}", ContentPillar.OPTIMIZATION,
                          ContentRole.GROWTH, "fixture"),
        ScoreBreakdown(**{key: 5 for key in SCORE_DIMENSIONS}),
        overall_score=10-i, rank=i,
    ) for i in range(1, 6)]
    research = ResearchResult(datetime.now(timezone.utc), "fixture", topics)
    research_run = Mock(return_value=research)
    monkeypatch.setattr(batch, "run_research_agent", research_run)
    monkeypatch.setattr(batch, "STATE_DIR", tmp_path / "state")
    for name in ["GeminiProvider", "PexelsProvider", "PixabayProvider", "EdgeTtsProvider"]:
        monkeypatch.setattr(batch, name, Mock())
    client = Mock()
    client.send_video.return_value = {"message_id": 1}
    monkeypatch.setattr(batch, "TelegramClient", Mock(return_value=client))
    youtube = Mock(side_effect=AssertionError("Batch must never construct a publisher"))
    monkeypatch.setattr("scripts.production.providers.youtube.YouTubeProvider", youtube)
    upload = Mock(side_effect=AssertionError("Batch must never upload"))
    monkeypatch.setattr("scripts.production.youtube_publishing.publish_approved_video", upload)
    brief = ContentBrief("PC gamers", "Noise", "Cooling checks", ["Hear your PC?", "Check fans."],
                         "Hear your PC?", "Recognizable problem")
    generate_brief = Mock(return_value=brief)
    monkeypatch.setattr(batch, "generate_content_brief", generate_brief)

    def generate(provider, scored, *, content_brief):
        assert content_brief is brief
        return VideoScript(scored.candidate.title, "growth", brief.selected_hook, "Check fans.",
                           [Scene(0, "Check fans.")], "Quiet PC", "A cooling guide.",
                           candidate_id=scored.candidate.candidate_id)

    def acquire(scenes, providers, directory, llm):
        path = directory / "asset.jpg"
        path.write_bytes(b"offline fixture")
        scene = scenes[0]
        scene.asset_path = path
        scene.production_mode = "real_visual"
        scene.asset_source = "pexels"
        scene.media_accepted = True

    def narrate(scenes, provider, directory):
        scenes[0].duration_seconds = 20
        scenes[0].audio_path = directory / "audio.mp3"
        return [SubtitleCue(0, 0, 20, "Check fans.")]

    def render(scenes, path, *, subtitle_path):
        assert subtitle_path.name == "captions.ass"
        path.write_bytes(b"offline video")
        return path

    monkeypatch.setattr(batch, "generate_script", generate)
    monkeypatch.setattr(batch, "acquire_assets", acquire)
    monkeypatch.setattr(batch, "generate_narration", narrate)
    monkeypatch.setattr(batch, "write_ass", Mock())
    monkeypatch.setattr(batch, "render_video", Mock(side_effect=render))
    monkeypatch.setattr(batch, "run_qa", Mock(return_value=QAResult(True)))
    kwargs = dict(gemini_api_key="fixture", pexels_api_key="fixture", pixabay_api_key="fixture",
                  telegram_bot_token="fixture", telegram_chat_id="fixture",
                  workdir=tmp_path / "work", output_dir=tmp_path / "output")
    yield dict(kwargs=kwargs, research=research, research_run=research_run, client=client,
               brief=generate_brief, topics=topics)
    youtube.assert_not_called()
    upload.assert_not_called()


@pytest.mark.parametrize("size", [1, 3])
def test_delivers_distinct_candidates_and_persists_metadata(rig, tmp_path, size):
    result = batch.run_batch(**rig["kwargs"], batch_size=size)
    assert len(result.completed) == size
    assert len({a.candidate_id for a in result.completed}) == size
    assert len({a.topic for a in result.completed}) == size
    assert [a.candidate_number for a in result.completed] == list(range(1, size + 1))
    assert result.blocker is None
    rig["research_run"].assert_called_once_with()
    assert rig["client"].send_video.call_count == size
    rig["client"].send_message.assert_called_once()
    assert "No candidate was auto-selected" in rig["client"].send_message.call_args.args[0]
    assert sorted(p.name for p in rig["kwargs"]["output_dir"].glob("*.mp4")) == [
        f"candidate_{i}.mp4" for i in range(1, size + 1)
    ]
    transient = rig["kwargs"]["workdir"] / "batch_metadata.json"
    persistent, = (tmp_path / "state" / "production_batch").glob("batch_*.json")
    assert persistent.read_text() == transient.read_text()
    assert json.loads(transient.read_text()) == result.to_dict()
    for attempt in result.completed:
        assert attempt.qa_passed is True
        assert attempt.duration_seconds == 20
        assert attempt.real_media_coverage == 1
        directory = rig["kwargs"]["workdir"] / "candidates" / f"candidate_{attempt.attempt_number}"
        assert (directory / "script.json").exists()
        assert (directory / "qa_result.json").exists()


@pytest.mark.parametrize("gate,status", [("check_visual_quality", "failed_visual"), ("run_qa", "failed_qa")])
def test_quality_failures_retry_next_candidate(rig, monkeypatch, gate, status):
    monkeypatch.setattr(batch, gate, Mock(side_effect=[QAResult(False, {"fixture": False})] + [QAResult(True)] * 3))
    result = batch.run_batch(**rig["kwargs"])
    assert result.attempts[0].status == status
    assert "FAILED" in result.attempts[0].failure_reason
    assert result.attempts[0].candidate_number is None
    assert len(result.completed) == 3
    assert result.completed[0].candidate_id == "2"
    assert rig["client"].send_video.call_count == 3


def test_external_blocker_preserves_success_and_sanitizes_error(rig):
    brief = rig["brief"].return_value
    rig["brief"].side_effect = [brief, requests.ConnectionError("https://secret-token.example")]
    result = batch.run_batch(**rig["kwargs"])
    assert len(result.completed) == 1
    assert len(result.attempts) == 2
    assert result.blocker == "ConnectionError"
    assert "secret-token" not in json.dumps(result.to_dict())
    assert result.attempts[1].status == "failed_exception"
    assert "⚠️ ConnectionError" in rig["client"].send_message.call_args.args[0]


def test_local_exception_retries(rig):
    brief = rig["brief"].return_value
    rig["brief"].side_effect = [ValueError("bad candidate"), brief, brief, brief]
    result = batch.run_batch(**rig["kwargs"])
    assert len(result.completed) == 3
    assert result.attempts[0].status == "failed_exception"
    assert result.blocker is None


def _rate_limit_error() -> RuntimeError:
    # Same sanitized shape a real Gemini 429 escapes _call_with_retry with
    # (see test_gemini_provider's text-fallback tier): no structured fields,
    # but _is_rate_limited matches the "Error code: 429" text.
    return RuntimeError(
        "Error code: 429 - Quota exceeded for "
        "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20"
        "\nPlease retry in 12s."
    )


def test_temporary_rate_limit_recovers_and_continues_to_next_candidate(rig, monkeypatch):
    """A transient 429 that survives a call's own retry budget must NOT abort
    the batch: after an explicit cooldown the next candidate is tried, and
    the batch still reaches batch_size (delivered candidates preserved)."""
    monkeypatch.setattr(batch, "RATE_LIMIT_RECOVERY_COOLDOWN_SECONDS", 0)
    brief = rig["brief"].return_value
    rig["brief"].side_effect = [_rate_limit_error(), brief, brief, brief]
    result = batch.run_batch(**rig["kwargs"])
    assert result.blocker is None
    assert len(result.completed) == 3
    assert result.attempts[0].status == "failed_rate_limit"
    assert [a.candidate_number for a in result.completed] == [1, 2, 3]
    assert rig["client"].send_video.call_count == 3


def test_rate_limit_recovery_preserves_delivered_candidate_before_failure(rig, monkeypatch):
    """Even when a later candidate hits a transient 429, the candidates
    already delivered to Telegram are kept and the batch still completes."""
    monkeypatch.setattr(batch, "RATE_LIMIT_RECOVERY_COOLDOWN_SECONDS", 0)
    brief = rig["brief"].return_value
    # Candidate 1 delivers normally, candidate 2 hits a 429 (one recovery
    # round), candidates 3+ deliver.
    rig["brief"].side_effect = [brief, _rate_limit_error(), brief, brief]
    result = batch.run_batch(**rig["kwargs"])
    assert result.blocker is None
    assert len(result.completed) == 3
    assert len(result.attempts) == 4
    assert result.attempts[1].status == "failed_rate_limit"
    assert [a.candidate_number for a in result.completed] == [1, 2, 3]
    assert len(rig["client"].send_video.call_args_list) == 3


def test_rate_limit_recovery_budget_exhausted_sets_blocker(rig, monkeypatch):
    """A persistent 429 is only promoted to a batch blocker after the bounded
    recovery budget (MAX_RATE_LIMIT_RECOVERIES rounds) is exhausted -- not on
    the first hit."""
    monkeypatch.setattr(batch, "RATE_LIMIT_RECOVERY_COOLDOWN_SECONDS", 0)
    rig["brief"].side_effect = [_rate_limit_error()] * 10
    result = batch.run_batch(**rig["kwargs"])
    assert not result.completed
    assert result.blocker is not None
    assert "rate limit" in result.blocker
    assert "recovery" in result.blocker
    assert len(result.attempts) == batch.MAX_RATE_LIMIT_RECOVERIES + 1
    assert all(a.status == "failed_rate_limit" for a in result.attempts[:-1])
    assert result.attempts[-1].status == "failed_exception"


@pytest.mark.parametrize("available", [0, 1])
def test_exhaustion_sets_blocker_and_sends_recap(rig, available):
    rig["research"].candidates[:] = rig["topics"][:available]
    result = batch.run_batch(**rig["kwargs"])
    assert len(result.completed) == available
    assert "exhausted" in result.blocker
    rig["client"].send_message.assert_called_once()
    if not available:
        assert "no deliverable candidates" in rig["client"].send_message.call_args.args[0]


def test_no_on_topic_candidate_records_failed_attempt(rig):
    for scored in rig["topics"]:
        scored.candidate.title = "Flower arrangements"
    result = batch.run_batch(**rig["kwargs"])
    assert not result.completed
    assert len(result.attempts) == 1
    assert result.attempts[0].failure_reason == "no on-topic candidate remaining"
    assert result.attempts[0].status == "failed_exception"
    assert "no more on-topic" in result.blocker
    rig["brief"].assert_not_called()


def test_research_paths_passed_once(rig, tmp_path):
    batch.run_batch(**rig["kwargs"], research_config_path=tmp_path / "config",
                    research_state_dir=tmp_path / "research")
    rig["research_run"].assert_called_once_with(config_path=tmp_path / "config", state_dir=tmp_path / "research")


@pytest.mark.parametrize("exception", [
    requests.ConnectionError(), requests.Timeout(), requests.HTTPError(),
    batch.VoiceGenerationError(), batch.AssetAcquisitionError(), batch.VideoAssemblyError(),
    RuntimeError("Error code: 429 RESOURCE_EXHAUSTED"),
    type("APIError", (Exception,), {"__module__": "google.genai.errors"})(),
])
def test_external_blocker_classification(exception):
    assert batch._is_external_blocker(exception)


def test_bad_brief_is_not_external_blocker():
    from scripts.production.providers.llm import ScriptGenerationError
    assert not batch._is_external_blocker(ScriptGenerationError("Bad JSON"))


@pytest.mark.parametrize("available", [0, 2])
def test_cli_with_mocked_providers(rig, monkeypatch, capsys, available):
    from scripts import produce_batch
    rig["research"].candidates[:] = rig["topics"][:available]
    preflight = Mock()
    monkeypatch.setattr(produce_batch, "run_preflight", preflight)
    for name in ["GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "TELEGRAM_BOT_TOKEN",
                 "TELEGRAM_CHAT_ID", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"]:
        monkeypatch.setenv(name, "offline")
    argv = ["--workdir", str(rig["kwargs"]["workdir"]), "--output-dir", str(rig["kwargs"]["output_dir"])]
    if available:
        produce_batch.main(argv)
    else:
        with pytest.raises(SystemExit) as error:
            produce_batch.main(argv)
        assert error.value.code == 1
    output = capsys.readouterr().out
    assert f"Delivered to Telegram: {available}/3" in output
    assert "Blocker:" in output
    if available:
        for label in ["Hook:", "Real-media coverage:", "duration:", "QA:"]:
            assert label in output
    assert preflight.call_args.kwargs["youtube_refresh_token"] == "offline"


def test_cli_preflight_failure_never_runs_batch(monkeypatch):
    from scripts import produce_batch
    from scripts.production.preflight import PreflightCheck

    error = produce_batch.PreflightError([PreflightCheck("Gemini", False, "missing key")])
    monkeypatch.setattr(produce_batch, "run_preflight", Mock(side_effect=error))
    run = Mock()
    monkeypatch.setattr(batch, "run_batch", run)
    with pytest.raises(SystemExit) as error:
        produce_batch.main([])
    assert error.value.code == 1
    run.assert_not_called()


def test_duplicate_ids_and_topics_are_not_delivered_twice(rig):
    from dataclasses import replace

    first = rig["topics"][0]
    rig["research"].candidates.extend([
        replace(first, candidate=replace(first.candidate, candidate_id="duplicate-topic")),
        replace(first, candidate=replace(first.candidate, title="PC duplicate ID")),
    ])
    result = batch.run_batch(**rig["kwargs"])
    assert len({item.topic for item in result.completed}) == 3
    assert len({item.candidate_id for item in result.completed}) == 3


def test_recap_failure_preserves_completed_result_and_metadata(rig, tmp_path):
    rig["client"].send_message.side_effect = requests.Timeout("secret-url")
    result = batch.run_batch(**rig["kwargs"])
    assert len(result.completed) == 3
    assert len(list((tmp_path / "state" / "production_batch").glob("*.json"))) == 1


def test_persist_metadata_sanitizes_batch_id(tmp_path):
    result = batch.BatchResult("2026-09-13T00:30:00Z/../x", "now", 3, [], [], "empty")
    path = batch.persist_batch_metadata(result, tmp_path)
    assert path.parent == tmp_path
    assert path.name == "batch_2026-09-13T00_30_00Z_.._x.json"
    assert json.loads(path.read_text()) == result.to_dict()


def test_workflow_is_manual_and_persists_only_batch_json():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".github/workflows/produce_batch.yml").read_text()
    assert "workflow_dispatch: {}" in text
    for forbidden in ["schedule:", "repository_dispatch:", "actions: write", "--force", "git add -A", "git add ."]:
        assert forbidden not in text
    assert "contents: write" in text
    assert "group: produce-video-batch" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 60" in text
    assert 'path.suffix != ".json"' in text
    assert "git add state/production_batch/" in text
    assert 'git push origin "HEAD:${TARGET_BRANCH}"' in text
    assert "python -m scripts.produce_batch" in text
    assert "python -m pytest -q" in text
