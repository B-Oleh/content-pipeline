"""Preflight: verifies every required secret/service is present and usable
before the production pipeline spends any time on research/rendering.

Never prints secret values -- only presence/absence and pass/fail per
service. Every check result message is built from safe, provider-supplied
error descriptions or the exception's type name only, never a raw request
URL or credential value (see providers/telegram_client.py and
providers/visual.py for where that risk actually lives and is handled).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

PING_QUERY = "gaming pc"  # innocuous, cheap query for the two visual-asset preflight checks


@dataclass
class PreflightCheck:
    name: str
    passed: bool
    message: str


class PreflightError(RuntimeError):
    def __init__(self, checks: list[PreflightCheck]) -> None:
        self.checks = checks
        failed = [c for c in checks if not c.passed]
        summary = "; ".join(f"{c.name}: {c.message}" for c in failed)
        super().__init__(f"Preflight failed ({len(failed)}/{len(checks)} check(s)): {summary}")


def _check_gemini(api_key: str | None) -> PreflightCheck:
    """Deliberately does NOT call the Gemini API (unlike the Pexels/Pixabay/
    Telegram checks below, which do a real live probe).

    script_agent.generate_script() makes a real Gemini call moments after
    preflight passes -- a separate `ping()` probe here would burn one of
    Gemini's free-tier quota units for nothing before the pipeline has done
    any real work, which is exactly what pushed a real run over Gemini's
    20-request free-tier limit (HTTP 429 "Quota exceeded ...
    generate_content_free_tier_requests"). The first real
    generate_script() call now serves as the actual connectivity test
    instead, and it has its own 429 retry/backoff (see
    providers/llm.py::_call_with_retry) -- a genuine auth/connectivity
    problem still surfaces immediately and clearly there, just one stage
    later than before.
    """
    if not api_key or not api_key.strip():
        return PreflightCheck("gemini", False, "GEMINI_API_KEY is not set")
    return PreflightCheck(
        "gemini", True, "Gemini API key is present (connectivity is verified by the first real request, not preflight)"
    )


def _check_pexels(api_key: str | None) -> PreflightCheck:
    if not api_key:
        return PreflightCheck("pexels", False, "PEXELS_API_KEY is not set")
    try:
        from scripts.production.providers.visual import PexelsProvider

        PexelsProvider(api_key).search_videos(PING_QUERY, per_page=1)
        return PreflightCheck("pexels", True, "Pexels video search works")
    except Exception as exc:  # noqa: BLE001
        return PreflightCheck("pexels", False, f"Pexels request failed ({_safe_error_message(exc)})")


def _check_pixabay(api_key: str | None) -> PreflightCheck:
    if not api_key:
        return PreflightCheck("pixabay", False, "PIXABAY_API_KEY is not set")
    try:
        from scripts.production.providers.visual import PixabayProvider

        PixabayProvider(api_key).search_videos(PING_QUERY, per_page=3)
        return PreflightCheck("pixabay", True, "Pixabay video search works")
    except Exception as exc:  # noqa: BLE001
        return PreflightCheck("pixabay", False, f"Pixabay request failed ({_safe_error_message(exc)})")


def _check_telegram(bot_token: str | None, chat_id: str | None) -> PreflightCheck:
    if not bot_token:
        return PreflightCheck("telegram", False, "TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        return PreflightCheck("telegram", False, "TELEGRAM_CHAT_ID is not set")
    try:
        from scripts.production.providers.telegram_client import TelegramClient

        TelegramClient(bot_token, chat_id).get_me()
        return PreflightCheck("telegram", True, "Telegram bot token is valid and reachable")
    except Exception as exc:  # noqa: BLE001 -- TelegramClient already sanitizes its own messages
        return PreflightCheck("telegram", False, f"Telegram check failed ({exc})")


def _check_youtube(client_id: str | None, client_secret: str | None, refresh_token: str | None) -> PreflightCheck:
    """Presence-only, like the Gemini check -- deliberately does NOT
    refresh an access token or call the YouTube Data API here (see the
    task's explicit "do not consume YouTube upload quota during preflight"
    / "the real authentication/upload is tested when Approve occurs"
    requirement, and CLAUDE.md's general "no paid/quota-consuming call
    before it's actually needed" spirit). A genuine OAuth/auth problem
    still surfaces clearly and immediately at Approve time via
    YouTubeConfigError (see providers/youtube.py).
    """
    missing = [
        name
        for name, value in (
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        )
        if not value or not value.strip()
    ]
    if missing:
        return PreflightCheck("youtube", False, f"{', '.join(missing)} not set")
    return PreflightCheck(
        "youtube", True, "YouTube OAuth secrets are present (refresh/upload is verified at Approve time, not preflight)"
    )


def _safe_error_message(exc: Exception) -> str:
    """A short, credential-safe description of a preflight failure that
    always names the underlying exception class, so e.g. an auth failure,
    a timeout, and a malformed response are never all reported identically.

    For this package's own exception types (GeminiPingError,
    ScriptGenerationError) the full message is safe to include verbatim --
    it is built only from the provider's own status/error fields (see
    providers/llm.py::_describe_incomplete_interaction), never from request
    URLs or credentials. For everything else, prefers a provider's own
    documented safe message field (e.g. google.genai.errors.APIError.message,
    which comes from the API's own JSON error body) and otherwise falls back
    to just the exception's type name -- never the raw str(exc), which can
    include request URLs/headers for lower-level transport exceptions.
    """
    from scripts.production.providers.llm import GeminiPingError, ScriptGenerationError

    class_name = type(exc).__name__
    if isinstance(exc, (GeminiPingError, ScriptGenerationError)):
        return f"{class_name}: {exc}"

    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        return f"{class_name}: {message}"
    return class_name


def run_preflight(
    *,
    gemini_api_key: str | None,
    pexels_api_key: str | None,
    pixabay_api_key: str | None,
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
    youtube_client_id: str | None = None,
    youtube_client_secret: str | None = None,
    youtube_refresh_token: str | None = None,
) -> list[PreflightCheck]:
    """Run every required check; raise PreflightError if any failed."""
    checks = [
        _check_gemini(gemini_api_key),
        _check_pexels(pexels_api_key),
        _check_pixabay(pixabay_api_key),
        _check_telegram(telegram_bot_token, telegram_chat_id),
        _check_youtube(youtube_client_id, youtube_client_secret, youtube_refresh_token),
    ]
    for check in checks:
        if check.passed:
            logger.info("Preflight OK: %s -- %s", check.name, check.message)
        else:
            logger.error("Preflight FAILED: %s -- %s", check.name, check.message)

    if any(not check.passed for check in checks):
        raise PreflightError(checks)
    return checks


def main() -> None:
    """Standalone entry point (python -m scripts.production.preflight) so
    the GitHub Actions workflow can run preflight as its own clearly
    labeled, independently-failing step -- see docs/PRODUCTION_PIPELINE.md.
    """
    try:
        run_preflight(
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            pexels_api_key=os.getenv("PEXELS_API_KEY"),
            pixabay_api_key=os.getenv("PIXABAY_API_KEY"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            youtube_client_id=os.getenv("YOUTUBE_CLIENT_ID"),
            youtube_client_secret=os.getenv("YOUTUBE_CLIENT_SECRET"),
            youtube_refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN"),
        )
    except PreflightError as exc:
        print(f"PREFLIGHT FAILED: {exc}")
        sys.exit(1)
    print("Preflight OK: Gemini API key present; Pexels, Pixabay, Telegram, and YouTube OAuth secrets are all present/reachable.")


if __name__ == "__main__":
    main()
