"""pipeline.py's Approve/Reject/Regenerate orchestration around YouTube
publishing -- no real Telegram, no real YouTube/Google API. `YouTubeProvider`
is monkeypatched at the `scripts.production.pipeline` import site (the
same pattern used throughout this codebase, e.g. test_preflight.py's
`GeminiProvider.__init__` "must not be constructed" guards), so these
tests prove pipeline.py's own wiring: which decisions trigger an upload
attempt, what gets passed to it, and what Telegram is told -- not
YouTube's OAuth/API mechanics (see test_youtube_provider.py) or the
idempotency logic itself (see test_youtube_publishing.py, exercised again
here end-to-end through a fake provider to prove pipeline.py wires the
real publish_approved_video() correctly).
"""

from __future__ import annotations

import pytest

from scripts.production.models import Scene, VideoScript
from scripts.production.pipeline import _handle_approve, _wait_for_approval
from scripts.production.providers.youtube import YouTubeConfigError, YouTubeUploadError, YouTubeUploadResult
from scripts.production.telegram_approval import ApprovalState


class _FakeTelegramClient:
    def __init__(self, updates_by_call: list[list[dict]] | None = None) -> None:
        self._updates_by_call = list(updates_by_call or [])
        self.messages_sent: list[str] = []

    def get_updates(self, offset=None, timeout=25):
        if not self._updates_by_call:
            return []
        return self._updates_by_call.pop(0)

    def answer_callback_query(self, callback_query_id, text=""):
        return {}

    def send_message(self, text):
        self.messages_sent.append(text)
        return {}


def _script(candidate_id: str = "content-42", title: str = "Myth 2: You Need The Top Flagship GPU", description: str = "A short description.") -> VideoScript:
    return VideoScript(
        topic="GPU myths", content_role="growth", hook="Did you know?", narration="...",
        scenes=[Scene(index=0, narration_line="line")], title=title, description=description, candidate_id=candidate_id,
    )


def _approval(content_id: str = "content-42", decision: str = "approve") -> ApprovalState:
    return ApprovalState(content_id=content_id, decision=decision)


YOUTUBE_CREDS = {"youtube_client_id": "cid", "youtube_client_secret": "csecret", "youtube_refresh_token": "rtoken"}


class _FakeYouTubeProvider:
    """Stands in for providers.youtube.YouTubeProvider -- records
    construction args and every upload_video() call, no real OAuth/API."""

    instances: list["_FakeYouTubeProvider"] = []

    def __init__(self, client_id, client_secret, refresh_token):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.upload_calls: list[dict] = []
        self._result = YouTubeUploadResult(video_id="vid1", youtube_url="https://www.youtube.com/watch?v=vid1", privacy_status="private")
        self._error: Exception | None = None
        type(self).instances.append(self)

    def upload_video(self, video_path, *, title, description, **kwargs):
        self.upload_calls.append({"video_path": video_path, "title": title, "description": description, **kwargs})
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture(autouse=True)
def _reset_fake_provider_instances():
    _FakeYouTubeProvider.instances = []
    yield
    _FakeYouTubeProvider.instances = []


def _must_not_construct(*args, **kwargs):
    raise AssertionError("YouTubeProvider must not be constructed for this decision")


# ---------------------------------------------------------------------------
# Approve invokes YouTube upload; Telegram receives the success confirmation
# ---------------------------------------------------------------------------


def test_approve_invokes_youtube_upload(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider", _FakeYouTubeProvider)
    client = _FakeTelegramClient()
    video_path = tmp_path / "output" / "final_video.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"fake mp4")

    _handle_approve(client, _script(), _approval(), video_path=video_path, workdir=tmp_path, **YOUTUBE_CREDS)

    assert len(_FakeYouTubeProvider.instances) == 1
    provider = _FakeYouTubeProvider.instances[0]
    assert len(provider.upload_calls) == 1


def test_approve_passes_the_exact_approved_video_path_to_the_uploader(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider", _FakeYouTubeProvider)
    client = _FakeTelegramClient()
    video_path = tmp_path / "output" / "final_video.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"fake mp4")

    _handle_approve(client, _script(), _approval(), video_path=video_path, workdir=tmp_path, **YOUTUBE_CREDS)

    call = _FakeYouTubeProvider.instances[0].upload_calls[0]
    assert call["video_path"] == video_path
    assert call["title"] == "Myth 2: You Need The Top Flagship GPU"
    assert call["description"] == "A short description."


def test_telegram_receives_successful_upload_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider", _FakeYouTubeProvider)
    client = _FakeTelegramClient()
    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4")

    _handle_approve(client, _script(), _approval(), video_path=video_path, workdir=tmp_path, **YOUTUBE_CREDS)

    assert len(client.messages_sent) == 1
    message = client.messages_sent[0]
    assert "✅ Approved and uploaded to YouTube" in message
    assert "Myth 2: You Need The Top Flagship GPU" in message
    assert "Private" in message
    assert "https://www.youtube.com/watch?v=vid1" in message
    assert "published publicly" not in message.lower()


# ---------------------------------------------------------------------------
# Reject / Regenerate never invoke YouTube upload
# ---------------------------------------------------------------------------


def test_reject_never_invokes_youtube_upload(monkeypatch):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider.__init__", _must_not_construct)
    monkeypatch.setattr("scripts.production.pipeline.poll_for_decision", lambda client, content_id, **kw: _approval(decision="reject"))
    client = _FakeTelegramClient()

    result = _wait_for_approval(
        client, _script(),
        github_token=None, github_repository=None, timeout_seconds=1,
        video_path="unused.mp4", workdir="unused",
        youtube_client_id="cid", youtube_client_secret="csecret", youtube_refresh_token="rtoken",
    )

    assert result.decision == "reject"


def test_regenerate_never_invokes_youtube_upload(monkeypatch):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider.__init__", _must_not_construct)
    monkeypatch.setattr("scripts.production.pipeline.poll_for_decision", lambda client, content_id, **kw: _approval(decision="regenerate"))
    monkeypatch.setattr("scripts.production.pipeline.trigger_regeneration_workflow", lambda **kw: None)
    client = _FakeTelegramClient()

    result = _wait_for_approval(
        client, _script(),
        github_token="ghtoken", github_repository="owner/repo", timeout_seconds=1,
        video_path="unused.mp4", workdir="unused",
        youtube_client_id="cid", youtube_client_secret="csecret", youtube_refresh_token="rtoken",
    )

    assert result.decision == "regenerate"


def test_timeout_never_invokes_youtube_upload(monkeypatch):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider.__init__", _must_not_construct)
    monkeypatch.setattr("scripts.production.pipeline.poll_for_decision", lambda client, content_id, **kw: _approval(decision="timeout"))
    client = _FakeTelegramClient()

    result = _wait_for_approval(
        client, _script(),
        github_token=None, github_repository=None, timeout_seconds=1,
        video_path="unused.mp4", workdir="unused",
        youtube_client_id="cid", youtube_client_secret="csecret", youtube_refresh_token="rtoken",
    )

    assert result.decision == "timeout"


# ---------------------------------------------------------------------------
# Duplicate Approve (real publish_approved_video/idempotency logic, fake
# provider only) does NOT upload twice
# ---------------------------------------------------------------------------


def test_duplicate_approve_does_not_upload_twice_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider", _FakeYouTubeProvider)
    client = _FakeTelegramClient()
    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4")

    _handle_approve(client, _script(), _approval(), video_path=video_path, workdir=tmp_path, **YOUTUBE_CREDS)
    _handle_approve(client, _script(), _approval(), video_path=video_path, workdir=tmp_path, **YOUTUBE_CREDS)

    total_uploads = sum(len(instance.upload_calls) for instance in _FakeYouTubeProvider.instances)
    assert total_uploads == 1
    assert len(client.messages_sent) == 2  # both presses get a confirmation ...
    assert client.messages_sent[0] == client.messages_sent[1]  # ... with the SAME (existing) YouTube URL


# ---------------------------------------------------------------------------
# Failure behavior
# ---------------------------------------------------------------------------


def test_failed_upload_sends_a_sanitized_telegram_error(monkeypatch, tmp_path):
    class _FailingProvider(_FakeYouTubeProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._error = YouTubeUploadError("HTTP 403: quotaExceeded")

    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider", _FailingProvider)
    client = _FakeTelegramClient()
    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4")

    _handle_approve(client, _script(), _approval(), video_path=video_path, workdir=tmp_path, **YOUTUBE_CREDS)

    assert len(client.messages_sent) == 1
    message = client.messages_sent[0]
    assert "⚠️" in message
    assert "YouTube upload failed" in message
    assert "quotaExceeded" in message
    assert "cid" not in message
    assert "csecret" not in message
    assert "rtoken" not in message


def test_failed_upload_does_not_persist_a_publication_record(monkeypatch, tmp_path):
    from scripts.production.youtube_publishing import load_publication_record

    class _FailingProvider(_FakeYouTubeProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._error = YouTubeUploadError("HTTP 500: internal error")

    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider", _FailingProvider)
    client = _FakeTelegramClient()
    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4")

    _handle_approve(client, _script(), _approval(), video_path=video_path, workdir=tmp_path, **YOUTUBE_CREDS)

    assert load_publication_record(tmp_path / "youtube_publications.json", "content-42") is None


def test_missing_youtube_config_sends_a_sanitized_telegram_error_without_constructing_provider(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider.__init__", _must_not_construct)
    client = _FakeTelegramClient()

    _handle_approve(
        client, _script(), _approval(), video_path=tmp_path / "final_video.mp4", workdir=tmp_path,
        youtube_client_id=None, youtube_client_secret=None, youtube_refresh_token=None,
    )

    assert len(client.messages_sent) == 1
    assert "YouTube upload failed" in client.messages_sent[0]


def test_content_id_mismatch_refuses_to_upload(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider.__init__", _must_not_construct)
    client = _FakeTelegramClient()

    mismatched_approval = _approval(content_id="some-other-content-id")
    _handle_approve(client, _script(), mismatched_approval, video_path=tmp_path / "final_video.mp4", workdir=tmp_path, **YOUTUBE_CREDS)

    assert len(client.messages_sent) == 1
    assert "did not match" in client.messages_sent[0]


def test_youtube_upload_error_message_is_the_only_thing_shown_never_a_raw_exception_repr(monkeypatch, tmp_path):
    """YouTubeConfigError should be handled the same way as
    YouTubeUploadError -- e.g. a refresh failure at Approve time."""

    class _RaisesConfigError:
        def __init__(self, *args, **kwargs):
            raise YouTubeConfigError("Could not refresh YouTube OAuth credentials (RefreshError)")

    monkeypatch.setattr("scripts.production.pipeline.YouTubeProvider", _RaisesConfigError)
    client = _FakeTelegramClient()
    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4")

    _handle_approve(client, _script(), _approval(), video_path=video_path, workdir=tmp_path, **YOUTUBE_CREDS)

    assert len(client.messages_sent) == 1
    assert "Could not refresh YouTube OAuth credentials" in client.messages_sent[0]
