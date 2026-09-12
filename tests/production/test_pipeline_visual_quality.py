"""Production retries must happen before rendering or external delivery."""
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from scripts.production import pipeline
from scripts.production.models import QAResult, Scene, SubtitleCue, VideoScript
from scripts.production.telegram_approval import ApprovalState
from scripts.production.visual_quality import VisualQualityError
from scripts.research.models import (
    ContentPillar, ContentRole, ResearchCandidate, ResearchResult,
    ScoreBreakdown, ScoredCandidate, SCORE_DIMENSIONS,
)


def candidate(name, rank):
    return ScoredCandidate(
        candidate=ResearchCandidate(name, f"PC cooling {name}", ContentPillar.OPTIMIZATION,
                                    ContentRole.GROWTH, "test"),
        scores=ScoreBreakdown(**{key: 5 for key in SCORE_DIMENSIONS}),
        overall_score=10-rank, rank=rank,
    )


@pytest.mark.parametrize("succeeds", [True, False])
def test_pipeline_retries_topic_before_render_and_delivery(tmp_path, monkeypatch, succeeds):
    topics = [candidate("first", 1), candidate("second", 2)]
    research = ResearchResult(datetime.now(timezone.utc), "test", topics)
    monkeypatch.setattr(pipeline, "run_research_agent", lambda **kw: research)
    for name in ["GeminiProvider", "PexelsProvider", "PixabayProvider", "EdgeTtsProvider", "TelegramClient"]:
        monkeypatch.setattr(pipeline, name, Mock())
    generated = []

    def generate(provider, scored):
        generated.append(scored.candidate.candidate_id)
        return VideoScript(scored.candidate.title, "growth", "hook", "narration",
                           [Scene(0, "PC cooling")], "title", "description",
                           candidate_id=scored.candidate.candidate_id)

    def acquire(scenes, providers, directory, llm):
        path = directory / "asset.jpg"
        path.write_bytes(b"acquired asset")
        scene = scenes[0]
        scene.asset_path = path
        scene.production_mode = "real_visual" if succeeds and len(generated) == 2 else "info_card"
        scene.asset_source = "pexels"
        scene.media_accepted = scene.production_mode == "real_visual"

    def narrate(scenes, provider, directory):
        scenes[0].duration_seconds = 20
        scenes[0].audio_path = directory / "audio.mp3"
        return [SubtitleCue(0, 0, 20, "PC cooling")]

    monkeypatch.setattr(pipeline, "generate_script", generate)
    monkeypatch.setattr(pipeline, "acquire_assets", acquire)
    monkeypatch.setattr(pipeline, "generate_narration", narrate)
    monkeypatch.setattr(pipeline, "write_ass", Mock())
    render = Mock(return_value=tmp_path / "final.mp4")
    deliver = Mock()
    monkeypatch.setattr(pipeline, "render_video", render)
    monkeypatch.setattr(pipeline, "deliver_video", deliver)
    monkeypatch.setattr(pipeline, "run_qa", Mock(return_value=QAResult(True)))
    monkeypatch.setattr(pipeline, "_wait_for_approval", Mock(return_value=ApprovalState("second", "reject")))
    kwargs = dict(gemini_api_key="test", pexels_api_key="test", pixabay_api_key="test",
                  telegram_bot_token="test", telegram_chat_id="test", workdir=tmp_path,
                  output_path=tmp_path / "final.mp4")
    if succeeds:
        result = pipeline.run_pipeline(**kwargs)
        assert result.script.candidate_id == "second"
        assert result.delivered
        render.assert_called_once()
        assert render.call_args.args[0][0].media_accepted
        assert deliver.call_args.args[2] == topics[1]
        assert '"media_accepted": true' in (tmp_path / "script.json").read_text()
    else:
        with pytest.raises(VisualQualityError):
            pipeline.run_pipeline(**kwargs)
        render.assert_not_called()
        deliver.assert_not_called()
    assert generated == ["first", "second"]
    assert (tmp_path / "visual_attempts.json").exists()
