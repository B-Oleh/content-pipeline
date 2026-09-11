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


def test_gemini_check_passes_with_a_present_key_without_any_network_call(monkeypatch):
    """The Gemini check is deliberately NOT a live probe (see
    preflight.py::_check_gemini docstring): script_agent.generate_script()
    makes a real Gemini call moments after preflight passes, so a separate
    ping() here would burn one of Gemini's scarce free-tier quota units for
    nothing -- this is the fix for a real "Quota exceeded ...
    generate_content_free_tier_requests, limit: 20" HTTP 429 mid-run.
    Proven here by making GeminiProvider.__init__ raise if it is ever
    constructed -- preflight must still pass with a present key.
    """

    def _must_not_be_constructed(self, *args, **kwargs):
        raise AssertionError("GeminiProvider must not be constructed during preflight")

    monkeypatch.setattr("scripts.production.providers.llm.GeminiProvider.__init__", _must_not_be_constructed)

    # Call the Gemini check directly (a full run_preflight() would also
    # raise for the other, still-missing services here, which is not what
    # this test is about).
    from scripts.production.preflight import _check_gemini

    check = _check_gemini("present-key-value")
    assert check.passed
    assert check.name == "gemini"


def test_gemini_check_fails_when_key_is_blank(monkeypatch):
    from scripts.production.preflight import _check_gemini

    check = _check_gemini("   ")
    assert not check.passed
    assert "GEMINI_API_KEY is not set" in check.message


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
