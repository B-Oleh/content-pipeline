"""Telegram Bot API client -- thin wrapper, no callback/webhook handling yet.

See CLAUDE.md "Telegram approval gate": full Approve/Regenerate/Reject
buttons are a later milestone (see docs/PRODUCTION_PIPELINE.md). For this
milestone the only requirement is that the real rendered video reaches
Telegram, so this client only implements getMe() (preflight) and
send_video().
"""

from __future__ import annotations

from pathlib import Path

import requests

DEFAULT_TIMEOUT_SECONDS = 60.0
# Generous timeout for the video upload itself -- a ~10-20MB short-form
# video can take a while on a slow connection; this is not a network probe.
UPLOAD_TIMEOUT_SECONDS = 180.0


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

    def send_video(self, video_path: Path, caption: str) -> dict:
        video_path = Path(video_path)
        try:
            with open(video_path, "rb") as video_file:
                response = requests.post(
                    f"{self._base_url}/sendVideo",
                    data={
                        "chat_id": self._chat_id,
                        "caption": caption[:1024],  # Telegram caption length limit
                        "supports_streaming": "true",
                    },
                    files={"video": (video_path.name, video_file, "video/mp4")},
                    timeout=UPLOAD_TIMEOUT_SECONDS,
                )
        except requests.exceptions.RequestException as exc:
            raise TelegramError(f"Telegram sendVideo request failed ({type(exc).__name__})") from None
        return self._parse_ok(response, "sendVideo")

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
