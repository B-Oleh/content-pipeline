"""YouTubeProvider tests -- no real network, no real Google OAuth/API
calls. `Credentials`/`build` are monkeypatched at the module level (same
pattern as test_gemini_provider.py's fake client injection and
test_visual_providers.py's monkeypatched `requests.get`), so these prove
the provider's own logic (config validation, credential construction,
upload request shape, error sanitization) without ever touching the real
Google APIs (see CLAUDE.md "Testing requirements").
"""

from __future__ import annotations

import logging

import pytest
from googleapiclient.errors import HttpError

from scripts.production.providers.youtube import (
    DEFAULT_PRIVACY_STATUS,
    TOKEN_URI,
    UPLOAD_SCOPE,
    YouTubeConfigError,
    YouTubeProvider,
    YouTubeUploadError,
)

FAKE_CLIENT_ID = "fake-client-id.apps.googleusercontent.com"
FAKE_CLIENT_SECRET = "fake-client-secret-should-never-appear-in-output"
FAKE_REFRESH_TOKEN = "1//fake-refresh-token-should-never-appear-in-output"


class _SpyCredentials:
    """Records exactly what YouTubeProvider passed to Credentials(), and
    what refresh() was called with -- without ever making a real token
    request."""

    last_kwargs: dict | None = None
    refresh_call_count = 0
    refresh_side_effect: Exception | None = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def refresh(self, request):
        type(self).refresh_call_count += 1
        if type(self).refresh_side_effect is not None:
            raise type(self).refresh_side_effect


def _reset_spy_credentials():
    _SpyCredentials.last_kwargs = None
    _SpyCredentials.refresh_call_count = 0
    _SpyCredentials.refresh_side_effect = None


class _FakeHttpResponse:
    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason


def _http_error(status: int = 403, message: str = "quotaExceeded") -> HttpError:
    import json

    content = json.dumps({"error": {"message": message}}).encode("utf-8")
    return HttpError(_FakeHttpResponse(status, message), content)


class _FakeRequest:
    """Stands in for the object googleapiclient's `.videos().insert(...)`
    returns -- `next_chunk()` is called in a loop by upload_video() until
    it returns a non-None response (or raises)."""

    def __init__(self, chunks: list) -> None:
        self._chunks = list(chunks)  # each entry: (status, response) tuple, or an Exception to raise
        self.next_chunk_calls = 0

    def next_chunk(self, num_retries: int = 0):
        self.next_chunk_calls += 1
        item = self._chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeVideosResource:
    def __init__(self, request: _FakeRequest) -> None:
        self._request = request
        self.insert_kwargs: dict | None = None

    def insert(self, **kwargs):
        self.insert_kwargs = kwargs
        return self._request


class _FakeYouTubeClient:
    def __init__(self, request: _FakeRequest) -> None:
        self._videos_resource = _FakeVideosResource(request)

    def videos(self):
        return self._videos_resource


def _patch_credential_construction(monkeypatch, *, refresh_side_effect: Exception | None = None):
    _reset_spy_credentials()
    _SpyCredentials.refresh_side_effect = refresh_side_effect
    monkeypatch.setattr("scripts.production.providers.youtube.Credentials", _SpyCredentials)


def _patch_build(monkeypatch, fake_client: _FakeYouTubeClient) -> dict:
    captured_build_kwargs: dict = {}

    def fake_build(service_name, version, **kwargs):
        captured_build_kwargs["service_name"] = service_name
        captured_build_kwargs["version"] = version
        captured_build_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr("scripts.production.providers.youtube.build", fake_build)
    return captured_build_kwargs


# ---------------------------------------------------------------------------
# 1. Missing OAuth secrets fail safely
# ---------------------------------------------------------------------------


def test_missing_client_id_raises_config_error_without_network():
    with pytest.raises(YouTubeConfigError, match="YOUTUBE_CLIENT_ID"):
        YouTubeProvider("", FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)


def test_missing_client_secret_raises_config_error():
    with pytest.raises(YouTubeConfigError, match="YOUTUBE_CLIENT_SECRET"):
        YouTubeProvider(FAKE_CLIENT_ID, "   ", FAKE_REFRESH_TOKEN)


def test_missing_refresh_token_raises_config_error():
    with pytest.raises(YouTubeConfigError, match="YOUTUBE_REFRESH_TOKEN"):
        YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, None)


def test_all_missing_reports_all_three():
    with pytest.raises(YouTubeConfigError) as exc_info:
        YouTubeProvider(None, None, None)
    message = str(exc_info.value)
    assert "YOUTUBE_CLIENT_ID" in message
    assert "YOUTUBE_CLIENT_SECRET" in message
    assert "YOUTUBE_REFRESH_TOKEN" in message


# ---------------------------------------------------------------------------
# 2. Refresh-token credentials are constructed correctly
# ---------------------------------------------------------------------------


def test_credentials_are_constructed_with_the_refresh_token_flow(monkeypatch):
    _patch_credential_construction(monkeypatch)
    _patch_build(monkeypatch, _FakeYouTubeClient(_FakeRequest([])))

    YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)

    kwargs = _SpyCredentials.last_kwargs
    assert kwargs["token"] is None  # no cached access token -- always refreshed fresh
    assert kwargs["refresh_token"] == FAKE_REFRESH_TOKEN
    assert kwargs["token_uri"] == "https://oauth2.googleapis.com/token"
    assert kwargs["token_uri"] == TOKEN_URI
    assert kwargs["client_id"] == FAKE_CLIENT_ID
    assert kwargs["client_secret"] == FAKE_CLIENT_SECRET
    assert kwargs["scopes"] == [UPLOAD_SCOPE]
    assert UPLOAD_SCOPE == "https://www.googleapis.com/auth/youtube.upload"
    assert _SpyCredentials.refresh_call_count == 1  # refreshed eagerly, not lazily on first API call


def test_no_interactive_browser_flow_or_client_secrets_file_is_used(monkeypatch):
    """Regression guard: this module must only ever import/construct
    Credentials + Request (refresh-token flow) -- never
    google_auth_oauthlib's InstalledAppFlow or any client_secrets.json
    reader, which would require an interactive browser and cannot run
    unattended in GitHub Actions."""
    import scripts.production.providers.youtube as youtube_module

    assert not hasattr(youtube_module, "InstalledAppFlow")
    assert not hasattr(youtube_module, "flow_from_clientsecrets")


# ---------------------------------------------------------------------------
# 3. Secrets are never logged
# ---------------------------------------------------------------------------


def test_secrets_never_appear_in_logs_on_successful_construction(monkeypatch, caplog):
    _patch_credential_construction(monkeypatch)
    _patch_build(monkeypatch, _FakeYouTubeClient(_FakeRequest([])))

    with caplog.at_level(logging.DEBUG):
        YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)

    assert FAKE_CLIENT_SECRET not in caplog.text
    assert FAKE_REFRESH_TOKEN not in caplog.text


def test_secrets_never_appear_in_logs_or_exception_on_refresh_failure(monkeypatch, caplog):
    _patch_credential_construction(monkeypatch, refresh_side_effect=Exception("invalid_grant: token revoked"))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(YouTubeConfigError) as exc_info:
            YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)

    assert FAKE_CLIENT_SECRET not in caplog.text
    assert FAKE_REFRESH_TOKEN not in caplog.text
    assert FAKE_CLIENT_SECRET not in str(exc_info.value)
    assert FAKE_REFRESH_TOKEN not in str(exc_info.value)


def test_secrets_never_appear_in_logs_on_upload_failure(monkeypatch, caplog, tmp_path):
    _patch_credential_construction(monkeypatch)
    fake_client = _FakeYouTubeClient(_FakeRequest([_http_error(403, "quotaExceeded")]))
    _patch_build(monkeypatch, fake_client)

    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    provider = YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(YouTubeUploadError) as exc_info:
            provider.upload_video(video_path, title="T", description="D")

    assert FAKE_CLIENT_SECRET not in caplog.text
    assert FAKE_REFRESH_TOKEN not in caplog.text
    assert FAKE_CLIENT_SECRET not in str(exc_info.value)
    assert FAKE_REFRESH_TOKEN not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Successful upload: stores video_id/URL, privacyStatus is explicitly "private"
# ---------------------------------------------------------------------------


def test_successful_upload_returns_video_id_and_canonical_url(monkeypatch, tmp_path):
    _patch_credential_construction(monkeypatch)
    fake_client = _FakeYouTubeClient(_FakeRequest([(None, {"id": "abc123XYZ", "status": {"privacyStatus": "private"}})]))
    _patch_build(monkeypatch, fake_client)

    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    provider = YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)
    result = provider.upload_video(video_path, title="My Title", description="My description")

    assert result.video_id == "abc123XYZ"
    assert result.youtube_url == "https://www.youtube.com/watch?v=abc123XYZ"
    assert result.privacy_status == "private"


def test_upload_request_uses_snippet_and_status_parts_with_private_visibility(monkeypatch, tmp_path):
    _patch_credential_construction(monkeypatch)
    fake_client = _FakeYouTubeClient(_FakeRequest([(None, {"id": "abc123", "status": {"privacyStatus": "private"}})]))
    _patch_build(monkeypatch, fake_client)

    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    provider = YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)
    provider.upload_video(video_path, title="Myth 2: You Need The Top Flagship GPU", description="A short description.")

    insert_kwargs = fake_client._videos_resource.insert_kwargs
    assert insert_kwargs["part"] == "snippet,status"
    body = insert_kwargs["body"]
    assert body["snippet"]["title"] == "Myth 2: You Need The Top Flagship GPU"
    assert body["snippet"]["description"] == "A short description."
    assert body["snippet"]["categoryId"] == "20"
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["privacyStatus"] == DEFAULT_PRIVACY_STATUS
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert "tags" not in body["snippet"]  # no tags in generated metadata -- none invented


def test_upload_never_defaults_to_a_non_private_visibility():
    """Explicit regression guard for the task's core compliance
    requirement: even if a caller forgets to pass privacy_status, the
    provider's own default must still be "private", never "public" or
    "unlisted"."""
    assert DEFAULT_PRIVACY_STATUS == "private"


def test_upload_includes_tags_when_provided(monkeypatch, tmp_path):
    _patch_credential_construction(monkeypatch)
    fake_client = _FakeYouTubeClient(_FakeRequest([(None, {"id": "abc123", "status": {"privacyStatus": "private"}})]))
    _patch_build(monkeypatch, fake_client)

    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    provider = YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)
    provider.upload_video(video_path, title="T", description="D", tags=["pc gaming", "gpu"])

    body = fake_client._videos_resource.insert_kwargs["body"]
    assert body["snippet"]["tags"] == ["pc gaming", "gpu"]


# ---------------------------------------------------------------------------
# Failure behavior: never marks a failed upload successful
# ---------------------------------------------------------------------------


def test_upload_raises_on_missing_video_file(monkeypatch, tmp_path):
    _patch_credential_construction(monkeypatch)
    _patch_build(monkeypatch, _FakeYouTubeClient(_FakeRequest([])))

    provider = YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)

    with pytest.raises(YouTubeUploadError, match="does not exist"):
        provider.upload_video(tmp_path / "missing.mp4", title="T", description="D")


def test_upload_http_error_raises_sanitized_youtube_upload_error(monkeypatch, tmp_path):
    _patch_credential_construction(monkeypatch)
    fake_client = _FakeYouTubeClient(_FakeRequest([_http_error(403, "quotaExceeded")]))
    _patch_build(monkeypatch, fake_client)

    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    provider = YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)

    with pytest.raises(YouTubeUploadError) as exc_info:
        provider.upload_video(video_path, title="T", description="D")

    assert "403" in str(exc_info.value)
    assert "quotaExceeded" in str(exc_info.value)


def test_upload_response_without_video_id_raises_upload_error(monkeypatch, tmp_path):
    _patch_credential_construction(monkeypatch)
    fake_client = _FakeYouTubeClient(_FakeRequest([(None, {"status": {"privacyStatus": "private"}})]))
    _patch_build(monkeypatch, fake_client)

    video_path = tmp_path / "final_video.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    provider = YouTubeProvider(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN)

    with pytest.raises(YouTubeUploadError, match="video id"):
        provider.upload_video(video_path, title="T", description="D")
