"""Telegram Bot API client, including inline-keyboard callback handling.

See CLAUDE.md "Telegram approval gate" and docs/PRODUCTION_PIPELINE.md
"Telegram approval gate" for how the methods below (get_updates,
answer_callback_query, send_message, and send_video's reply_markup) are
used by telegram_approval.py to implement real Approve/Regenerate/Reject
buttons -- not just send them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import requests

DEFAULT_TIMEOUT_SECONDS = 60.0
# Generous timeout for the video upload itself -- a ~10-20MB short-form
# video can take a while on a slow connection; this is not a network probe.
UPLOAD_TIMEOUT_SECONDS = 180.0
# getUpdates uses Telegram's own server-side long polling: one request
# blocks for up to this many seconds waiting for a new update before
# returning empty, so a bounded wait loop (see telegram_approval.py) makes
# very few requests instead of busy-polling (see CLAUDE.md "Cloud execution
# and budget": "favor event-driven ... over polling loops").
DEFAULT_LONG_POLL_TIMEOUT_SECONDS = 25


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        # The bot token lives in this base URL, which is why every method
        # below is careful never to let a raw requests exception (whose
        # string form typically includes the full request URL) propagate or
        # get logged -- that would print the bot token. Never print secret
        # values (see CLAUDE.md "Logging requirements" and this task's
        # preflight rule).
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._chat_id = chat_id
        self._timeout = timeout

    def get_me(self) -> dict:
        """Used by preflight.py to confirm the bot token is valid and reachable."""
        try:
            response = requests.get(f"{self._base_url}/getMe", timeout=self._timeout)
        except requests.exceptions.RequestException as exc:
            raise TelegramError(f"Telegram getMe request failed ({type(exc).__name__})") from None
        return self._parse_ok(response, "getMe")

    def send_video(self, video_path: Path, caption: str, reply_markup: Optional[dict] = None) -> dict:
        video_path = Path(video_path)
        data = {
            "chat_id": self._chat_id,
            "caption": caption[:1024],  # Telegram caption length limit
            "supports_streaming": "true",
        }
        if reply_markup is not None:
            # Telegram's Bot API takes reply_markup as a JSON-encoded string
            # even in a multipart/form-data body (there is no nested-object
            # form field type) -- see build_approval_keyboard() in
            # telegram_approval.py for what this dict looks like.
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            with open(video_path, "rb") as video_file:
                response = requests.post(
                    f"{self._base_url}/sendVideo",
                    data=data,
                    files={"video": (video_path.name, video_file, "video/mp4")},
                    timeout=UPLOAD_TIMEOUT_SECONDS,
                )
        except requests.exceptions.RequestException as exc:
            raise TelegramError(f"Telegram sendVideo request failed ({type(exc).__name__})") from None
        return self._parse_ok(response, "sendVideo")

    def send_message(self, text: str) -> dict:
        try:
            response = requests.post(
                f"{self._base_url}/sendMessage",
                data={"chat_id": self._chat_id, "text": text[:4096]},
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise TelegramError(f"Telegram sendMessage request failed ({type(exc).__name__})") from None
        return self._parse_ok(response, "sendMessage")

    def get_updates(self, offset: Optional[int] = None, timeout: int = DEFAULT_LONG_POLL_TIMEOUT_SECONDS) -> list[dict]:
        """Long-poll for new updates (callback_query in particular).

        Telegram blocks this single HTTP request server-side for up to
        `timeout` seconds if there is nothing new, so calling this in a
        loop with `offset` advanced past every update already seen is real
        long polling, not a tight busy loop (see telegram_approval.py).
        """
        params: dict = {"timeout": timeout, "allowed_updates": '["callback_query"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            response = requests.get(
                f"{self._base_url}/getUpdates",
                params=params,
                timeout=timeout + self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise TelegramError(f"Telegram getUpdates request failed ({type(exc).__name__})") from None
        return self._parse_ok(response, "getUpdates")

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> dict:
        """Clears the button's client-side "loading" spinner. Must be
        called for every received callback_query, whether or not it ends up
        being acted on."""
        try:
            response = requests.post(
                f"{self._base_url}/answerCallbackQuery",
                data={"callback_query_id": callback_query_id, "text": text[:200]},
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise TelegramError(f"Telegram answerCallbackQuery request failed ({type(exc).__name__})") from None
        return self._parse_ok(response, "answerCallbackQuery")

    @staticmethod
    def _parse_ok(response: requests.Response, method: str) -> dict:
        try:
            data = response.json()
        except ValueError:
            # Deliberately not including response.text or response.url --
            # either could echo back parts of the request.
            raise TelegramError(f"Telegram {method} returned a non-JSON response (HTTP {response.status_code})") from None
        if not response.ok or not data.get("ok"):
            raise TelegramError(f"Telegram {method} failed: {data.get('description', f'HTTP {response.status_code}')}")
        return data["result"]
