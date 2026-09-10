"""Preflight tests never make real network calls (see CLAUDE.md testing
requirements): missing-key paths short-circuit before any request, and the
"key present but the call fails" paths are exercised by monkeypatching the
provider classes rather than hitting the real services. The "key is present
and the real service responds" path is only exercisable for real in the
GitHub Actions workflow itself (see docs/PRODUCTION_PIPELINE.md).
"""

import pytest

from scripts.production.preflight import PreflightError, run_preflight


def test_run_preflight_raises_when_all_keys_missing():
    with pytest.raises(PreflightError) as exc_info:
        run_preflight(
            gemini_api_key=None,
            pexels_api_key=None,
            pixabay_api_key=None,
            telegram_bot_token=None,
            telegram_chat_id=None,
        )
    message = str(exc_info.value)
    assert "gemini" in message
    assert "pexels" in message
    assert "pixabay" in message
    assert "telegram" in message


def test_telegram_check_requires_both_token_and_chat_id():
    with pytest.raises(PreflightError) as exc_info:
        run_preflight(
            gemini_api_key=None,
            pexels_api_key=None,
            pixabay_api_key=None,
            telegram_bot_token="a-token",
            telegram_chat_id=None,
        )
    assert "TELEGRAM_CHAT_ID" in str(exc_info.value)


def test_missing_key_failure_message_never_contains_the_env_var_value():
    # Passing a key-shaped string as the env VAR NAME check is irrelevant here --
    # what matters is that a present-but-wrong key's value never appears in
    # the summary. Simulate that via monkeypatched providers below instead of
    # a real network call.
    with pytest.raises(PreflightError) as exc_info:
        run_preflight(
            gemini_api_key=None,
            pexels_api_key=None,
            pixabay_api_key=None,
            telegram_bot_token=None,
            telegram_chat_id=None,
        )
    assert "GEMINI_API_KEY is not set" in str(exc_info.value)


def test_pexels_failure_with_a_present_key_never_leaks_the_key(monkeypatch):
    fake_key = "pexels-fake-key-should-never-appear-in-output"

    def _boom(self, query, per_page=1):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr("scripts.production.providers.visual.PexelsProvider.search_videos", _boom)

    with pytest.raises(PreflightError) as exc_info:
        run_preflight(
            gemini_api_key=None,
            pexels_api_key=fake_key,
            pixabay_api_key=None,
            telegram_bot_token=None,
            telegram_chat_id=None,
        )
    assert fake_key not in str(exc_info.value)
    assert "pexels" in str(exc_info.value)


def test_gemini_failure_with_a_present_key_never_leaks_the_key(monkeypatch):
    fake_key = "AIzaSyFAKE-should-never-appear-in-output"

    def _boom(self):
        raise RuntimeError("simulated auth failure")

    monkeypatch.setattr("scripts.production.providers.llm.GeminiProvider.__init__", lambda self, api_key, model="x": None)
    monkeypatch.setattr("scripts.production.providers.llm.GeminiProvider.ping", _boom)

    with pytest.raises(PreflightError) as exc_info:
        run_preflight(
            gemini_api_key=fake_key,
            pexels_api_key=None,
            pixabay_api_key=None,
            telegram_bot_token=None,
            telegram_chat_id=None,
        )
    assert fake_key not in str(exc_info.value)


def test_telegram_failure_with_present_credentials_never_leaks_token(monkeypatch):
    fake_token = "123456:fake-telegram-token-should-not-leak"

    def _boom(self):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("scripts.production.providers.telegram_client.TelegramClient.__init__", lambda self, bot_token, chat_id, timeout=60.0: None)
    monkeypatch.setattr("scripts.production.providers.telegram_client.TelegramClient.get_me", _boom)

    with pytest.raises(PreflightError) as exc_info:
        run_preflight(
            gemini_api_key=None,
            pexels_api_key=None,
            pixabay_api_key=None,
            telegram_bot_token=fake_token,
            telegram_chat_id="12345",
        )
    assert fake_token not in str(exc_info.value)
