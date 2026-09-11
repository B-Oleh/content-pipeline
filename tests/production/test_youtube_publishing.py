"""youtube_publishing.py tests -- the idempotency/state layer wrapping
YouTubeProvider. Uses a fake provider (no real Google API, no real
YouTubeProvider construction) so these prove the "check existing, else
upload, then persist" logic in isolation from OAuth/network concerns
(those are covered in test_youtube_provider.py).
"""

from __future__ import annotations

import json

import pytest

from scripts.production.providers.youtube import YouTubeUploadError, YouTubeUploadResult
from scripts.production.youtube_publishing import (
    PublicationRecord,
    load_publication_record,
    publish_approved_video,
    save_publication_record,
)


class _FakeYouTubeProvider:
    """Records every upload_video() call; returns a pre-programmed result
    or raises a pre-programmed error."""

    def __init__(self, result: YouTubeUploadResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.upload_calls: list[dict] = []

    def upload_video(self, video_path, *, title, description, **kwargs):
        self.upload_calls.append({"video_path": video_path, "title": title, "description": description, **kwargs})
        if self._error is not None:
            raise self._error
        return self._result


def _result(video_id="abc123") -> YouTubeUploadResult:
    return YouTubeUploadResult(video_id=video_id, youtube_url=f"https://www.youtube.com/watch?v={video_id}", privacy_status="private")


# ---------------------------------------------------------------------------
# Record persistence
# ---------------------------------------------------------------------------


def test_save_and_load_publication_record_round_trips(tmp_path):
    path = tmp_path / "youtube_publications.json"
    record = PublicationRecord(
        content_id="c1", decision="approve", youtube_video_id="abc123",
        youtube_url="https://www.youtube.com/watch?v=abc123", privacy_status="private", uploaded_at="2026-01-01T00:00:00+00:00",
    )

    save_publication_record(path, record)
    loaded = load_publication_record(path, "c1")

    assert loaded == record


def test_load_publication_record_returns_none_when_missing(tmp_path):
    assert load_publication_record(tmp_path / "does_not_exist.json", "c1") is None


def test_save_publication_record_does_not_clobber_other_content_ids(tmp_path):
    path = tmp_path / "youtube_publications.json"
    first = PublicationRecord(content_id="c1", decision="approve", youtube_video_id="v1", youtube_url="url1", privacy_status="private", uploaded_at="t1")
    second = PublicationRecord(content_id="c2", decision="approve", youtube_video_id="v2", youtube_url="url2", privacy_status="private", uploaded_at="t2")

    save_publication_record(path, first)
    save_publication_record(path, second)

    assert load_publication_record(path, "c1") == first
    assert load_publication_record(path, "c2") == second


def test_load_publication_record_handles_corrupt_state_file_gracefully(tmp_path):
    path = tmp_path / "youtube_publications.json"
    path.write_text("not valid json {{{", encoding="utf-8")

    assert load_publication_record(path, "c1") is None


# ---------------------------------------------------------------------------
# publish_approved_video: the actual idempotency guard
# ---------------------------------------------------------------------------


def test_publish_approved_video_uploads_when_no_existing_record(tmp_path):
    state_path = tmp_path / "youtube_publications.json"
    provider = _FakeYouTubeProvider(result=_result("newvideo1"))

    record = publish_approved_video(
        provider, tmp_path / "final_video.mp4",
        content_id="content-1", title="T", description="D", state_path=state_path,
    )

    assert record.youtube_video_id == "newvideo1"
    assert record.youtube_url == "https://www.youtube.com/watch?v=newvideo1"
    assert record.privacy_status == "private"
    assert len(provider.upload_calls) == 1


def test_publish_approved_video_passes_the_exact_video_path_title_and_description(tmp_path):
    state_path = tmp_path / "youtube_publications.json"
    provider = _FakeYouTubeProvider(result=_result())
    video_path = tmp_path / "output" / "final_video.mp4"

    publish_approved_video(
        provider, video_path,
        content_id="content-1", title="Myth 2: You Need The Top Flagship GPU", description="A short description.",
        state_path=state_path,
    )

    call = provider.upload_calls[0]
    assert call["video_path"] == video_path
    assert call["title"] == "Myth 2: You Need The Top Flagship GPU"
    assert call["description"] == "A short description."


def test_publish_approved_video_always_uses_private_visibility(tmp_path):
    state_path = tmp_path / "youtube_publications.json"
    provider = _FakeYouTubeProvider(result=_result())

    publish_approved_video(provider, tmp_path / "final_video.mp4", content_id="c1", title="T", description="D", state_path=state_path)

    assert provider.upload_calls[0]["privacy_status"] == "private"


def test_publish_approved_video_persists_a_record_after_success(tmp_path):
    state_path = tmp_path / "youtube_publications.json"
    provider = _FakeYouTubeProvider(result=_result("videoXYZ"))

    publish_approved_video(provider, tmp_path / "final_video.mp4", content_id="content-1", title="T", description="D", state_path=state_path)

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["content-1"]["youtube_video_id"] == "videoXYZ"
    assert persisted["content-1"]["privacy_status"] == "private"
    assert "uploaded_at" in persisted["content-1"]


def test_duplicate_approve_does_not_upload_twice(tmp_path):
    """The critical idempotency requirement: a second call for the same
    content_id must not call upload_video() again."""
    state_path = tmp_path / "youtube_publications.json"
    provider = _FakeYouTubeProvider(result=_result("videoXYZ"))

    first = publish_approved_video(provider, tmp_path / "final_video.mp4", content_id="content-1", title="T", description="D", state_path=state_path)
    second = publish_approved_video(provider, tmp_path / "final_video.mp4", content_id="content-1", title="T", description="D", state_path=state_path)

    assert len(provider.upload_calls) == 1  # NOT 2
    assert second == first


def test_duplicate_approve_returns_the_existing_youtube_url(tmp_path):
    state_path = tmp_path / "youtube_publications.json"
    provider = _FakeYouTubeProvider(result=_result("videoXYZ"))

    publish_approved_video(provider, tmp_path / "final_video.mp4", content_id="content-1", title="T", description="D", state_path=state_path)
    second = publish_approved_video(provider, tmp_path / "final_video.mp4", content_id="content-1", title="T", description="D", state_path=state_path)

    assert second.youtube_url == "https://www.youtube.com/watch?v=videoXYZ"


def test_different_content_ids_each_get_their_own_upload(tmp_path):
    state_path = tmp_path / "youtube_publications.json"
    provider = _FakeYouTubeProvider(result=_result("v1"))

    publish_approved_video(provider, tmp_path / "a.mp4", content_id="content-A", title="A", description="D", state_path=state_path)
    provider._result = _result("v2")
    publish_approved_video(provider, tmp_path / "b.mp4", content_id="content-B", title="B", description="D", state_path=state_path)

    assert len(provider.upload_calls) == 2


# ---------------------------------------------------------------------------
# Failure behavior: a failed upload never marks publication successful
# ---------------------------------------------------------------------------


def test_failed_upload_does_not_persist_a_record(tmp_path):
    state_path = tmp_path / "youtube_publications.json"
    provider = _FakeYouTubeProvider(error=YouTubeUploadError("HTTP 403: quotaExceeded"))

    with pytest.raises(YouTubeUploadError):
        publish_approved_video(provider, tmp_path / "final_video.mp4", content_id="content-1", title="T", description="D", state_path=state_path)

    assert load_publication_record(state_path, "content-1") is None
    assert not state_path.exists()


def test_failed_upload_can_be_retried_and_succeed(tmp_path):
    """After a failed upload (no record persisted), a later Approve retry
    for the same content_id must actually attempt the upload again -- not
    be treated as already-published."""
    state_path = tmp_path / "youtube_publications.json"
    failing_provider = _FakeYouTubeProvider(error=YouTubeUploadError("HTTP 500: internal error"))

    with pytest.raises(YouTubeUploadError):
        publish_approved_video(failing_provider, tmp_path / "final_video.mp4", content_id="content-1", title="T", description="D", state_path=state_path)

    succeeding_provider = _FakeYouTubeProvider(result=_result("recovered1"))
    record = publish_approved_video(succeeding_provider, tmp_path / "final_video.mp4", content_id="content-1", title="T", description="D", state_path=state_path)

    assert record.youtube_video_id == "recovered1"
    assert len(succeeding_provider.upload_calls) == 1
