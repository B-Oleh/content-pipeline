from __future__ import annotations

import requests

from scripts.production.providers.telegram_client import TelegramClient, TelegramError

SECRET_TOKEN = "123456:AAsecret-bot-token-value"


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, ok=True):
        self.status_code = status_code
        self.ok = ok
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def test_get_me_success(monkeypatch):
    def fake_get(url, timeout=None):
        assert SECRET_TOKEN in url  # the client does need it in the URL to call Telegram
        return _FakeResponse(200, {"ok": True, "result": {"username": "test_bot"}})

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.get", fake_get)
    result = TelegramClient(SECRET_TOKEN, "12345").get_me()
    assert result["username"] == "test_bot"


def test_get_me_api_failure_raises_telegram_error_without_leaking_token(monkeypatch):
    def fake_get(url, timeout=None):
        return _FakeResponse(200, {"ok": False, "description": "Unauthorized"}, ok=True)

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.get", fake_get)
    try:
        TelegramClient(SECRET_TOKEN, "12345").get_me()
        raise AssertionError("expected TelegramError")
    except TelegramError as exc:
        assert SECRET_TOKEN not in str(exc)
        assert "Unauthorized" in str(exc)


def test_get_me_connection_error_never_leaks_token_in_exception(monkeypatch):
    """requests.exceptions.ConnectionError's default string form includes the
    full request URL (which contains the bot token) -- the client must never
    let that raw exception propagate or be stringified. See
    providers/telegram_client.py."""

    def fake_get(url, timeout=None):
        raise requests.exceptions.ConnectionError(f"Failed to connect to {url}")

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.get", fake_get)

    try:
        TelegramClient(SECRET_TOKEN, "12345").get_me()
        raise AssertionError("expected TelegramError")
    except TelegramError as exc:
        assert SECRET_TOKEN not in str(exc)


def test_send_video_success(monkeypatch, tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    def fake_post(url, data=None, files=None, timeout=None):
        assert data["chat_id"] == "12345"
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 42}})

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.post", fake_post)
    result = TelegramClient(SECRET_TOKEN, "12345").send_video(video_path, "caption text")
    assert result["message_id"] == 42


def test_send_video_failure_never_leaks_token(monkeypatch, tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    def fake_post(url, data=None, files=None, timeout=None):
        raise requests.exceptions.Timeout(f"Timed out calling {url}")

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.post", fake_post)

    try:
        TelegramClient(SECRET_TOKEN, "12345").send_video(video_path, "caption")
        raise AssertionError("expected TelegramError")
    except TelegramError as exc:
        assert SECRET_TOKEN not in str(exc)
