"""Unit tests for Pexels/Pixabay response parsing, using mocked HTTP -- no
public internet required (see CLAUDE.md testing requirements)."""

from __future__ import annotations

import pytest

from scripts.production.providers.visual import PexelsProvider, PixabayProvider

SECRET_PIXABAY_KEY = "super-secret-pixabay-key-12345"


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = "", url: str = "") -> None:
        self.status_code = status_code
        self.ok = status_code < 400
        self._json_data = json_data or {}
        self.text = text
        self.url = url

    def json(self):
        return self._json_data


def test_pexels_search_videos_parses_response(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None):
        assert headers["Authorization"] == "pexels-key"
        return _FakeResponse(
            200,
            {
                "videos": [
                    {
                        "url": "https://www.pexels.com/video/1",
                        "duration": 12,
                        "user": {"name": "Jane"},
                        "video_files": [
                            {"file_type": "video/mp4", "width": 640, "height": 1136, "link": "https://cdn/small.mp4"},
                            {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://cdn/large.mp4"},
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr("scripts.production.providers.visual.requests.get", fake_get)
    provider = PexelsProvider("pexels-key")
    results = provider.search_videos("gaming pc")

    assert len(results) == 1
    assert results[0].provider == "pexels"
    assert results[0].is_video is True
    assert results[0].download_url == "https://cdn/large.mp4"
    assert "Jane" in results[0].attribution


def test_pexels_search_videos_skips_entries_without_mp4(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(200, {"videos": [{"video_files": [{"file_type": "video/webm", "width": 640}]}]})

    monkeypatch.setattr("scripts.production.providers.visual.requests.get", fake_get)
    results = PexelsProvider("k").search_videos("q")
    assert results == []


def test_pexels_search_photos_fallback_parses_response(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(
            200,
            {"photos": [{"url": "https://pexels.com/photo/1", "width": 1080, "height": 1920, "photographer": "Bob", "src": {"large2x": "https://cdn/photo.jpg"}}]},
        )

    monkeypatch.setattr("scripts.production.providers.visual.requests.get", fake_get)
    results = PexelsProvider("k").search_photos("q")
    assert len(results) == 1
    assert results[0].is_video is False
    assert results[0].download_url == "https://cdn/photo.jpg"


def test_pixabay_search_videos_parses_response(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert params["key"] == SECRET_PIXABAY_KEY
        return _FakeResponse(
            200,
            {
                "hits": [
                    {
                        "pageURL": "https://pixabay.com/videos/1",
                        "duration": 10,
                        "user": "Alice",
                        "videos": {"medium": {"url": "https://cdn/medium.mp4", "width": 960, "height": 1706}},
                    }
                ]
            },
        )

    monkeypatch.setattr("scripts.production.providers.visual.requests.get", fake_get)
    results = PixabayProvider(SECRET_PIXABAY_KEY).search_videos("gaming pc")
    assert len(results) == 1
    assert results[0].provider == "pixabay"
    assert results[0].download_url == "https://cdn/medium.mp4"


def test_pixabay_error_response_never_leaks_api_key_in_exception(monkeypatch):
    """The Pixabay API key travels as a `key=` query parameter -- a naive
    response.raise_for_status() would embed the full request URL (including
    the key) in the exception message. See providers/visual.py::_raise_for_status.
    """

    def fake_get(url, params=None, timeout=None):
        # Simulate what `requests` would build internally: a URL containing the key.
        full_url = f"{url}?key={params['key']}&q=test"
        return _FakeResponse(401, text="Unauthorized", url=full_url)

    monkeypatch.setattr("scripts.production.providers.visual.requests.get", fake_get)

    with pytest.raises(Exception) as exc_info:
        PixabayProvider(SECRET_PIXABAY_KEY).search_videos("test")

    assert SECRET_PIXABAY_KEY not in str(exc_info.value)


def test_pexels_error_response_message_has_no_secret_leak_path(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(403, text="Forbidden")

    monkeypatch.setattr("scripts.production.providers.visual.requests.get", fake_get)

    with pytest.raises(Exception) as exc_info:
        PexelsProvider("pexels-secret-key").search_videos("q")

    assert "pexels-secret-key" not in str(exc_info.value)
