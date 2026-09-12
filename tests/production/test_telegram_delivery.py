from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.production.content_brief import ContentBrief
from scripts.production.models import VideoScript
from scripts.production.telegram_delivery import build_batch_caption, build_caption, deliver_video
from tests.production.test_script_agent import _scored_candidate


def script():
    return VideoScript("PC cooling", "growth", "Hook", "Narration", [], "Quiet PC", "Description", candidate_id="c1")


@pytest.mark.parametrize("passed", [True, False])
def test_batch_caption_has_summary_and_base_caption(passed):
    video = script()
    scored = _scored_candidate()
    brief = ContentBrief("PC gamers", "Noise", "Fans", ["Hear your PC?", "Check fans."], "Hear your PC?", "Pain")
    caption = build_batch_caption(video, scored, candidate_number=2, batch_size=3,
                                  content_brief=brief, duration_seconds=32.15,
                                  real_media_coverage=0.85, qa_passed=passed)
    assert caption.startswith("🎬 Candidate 2/3\n")
    for text in ["🎮 Topic: PC cooling", "👥 Target audience: PC gamers", "🪝 Hook: Hear your PC?",
                 "⏱ Duration: 32.1s", "🎞 Real-media coverage: 85%",
                 "✅ QA: passed" if passed else "❌ QA: failed"]:
        assert text in caption
    assert caption.endswith("\n\n" + build_caption(video, scored))


@pytest.mark.parametrize("caption", [None, "Custom batch caption", ""])
def test_deliver_uses_optional_caption_and_preserves_keyboard(caption):
    client = Mock()
    video = script()
    scored = _scored_candidate()
    result = deliver_video(Path("video.mp4"), video, scored, client, caption=caption)
    assert result is client.send_video.return_value
    call = client.send_video.call_args
    assert call.args[1] == (build_caption(video, scored) if caption is None else caption)
    assert "inline_keyboard" in call.kwargs["reply_markup"]


def test_custom_caption_does_not_bypass_candidate_id_check():
    video = script()
    video.candidate_id = ""
    client = Mock()
    with pytest.raises(ValueError):
        deliver_video(Path("video.mp4"), video, _scored_candidate(), client, caption="Batch")
    client.send_video.assert_not_called()
