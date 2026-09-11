"""YouTube Data API v3 publishing provider.

Thin and replaceable per CLAUDE.md "Provider abstraction": callers only
ever use YouTubeProvider.upload_video(), never the googleapiclient/
google-auth objects directly. Contains NO Telegram-specific logic and no
idempotency/state bookkeeping -- see youtube_publishing.py for the
"don't upload the same content_id twice" logic that wraps this provider,
and pipeline.py for the Telegram-facing glue.

**OAuth: refresh-token flow only, no interactive browser authorization.**
YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET/YOUTUBE_REFRESH_TOKEN (all from
GitHub Secrets in CI / `.env` locally -- see CLAUDE.md "Environment
variables and secret handling") are used to mint a short-lived access
token at runtime via `google.oauth2.credentials.Credentials` +
`google.auth.transport.requests.Request()`. No client_secrets.json file is
read, and no browser/consent-screen flow ever runs -- required for this to
work unattended inside GitHub Actions. Scope is fixed to
`https://www.googleapis.com/auth/youtube.upload`, matching the OAuth
consent already granted (see docs/PRODUCTION_PIPELINE.md "YouTube
publishing").

**Privacy: private only, for this milestone.** Google restricts
`videos.insert` uploads from API projects created after 2020-07-28 to
PRIVATE visibility until the project passes a YouTube API compliance
audit. `DEFAULT_PRIVACY_STATUS` is "private" and every caller in this
codebase uses it explicitly -- see the task's explicit "do not pretend the
video is public" requirement. Making a video public is a separate,
explicitly out-of-scope milestone (needs the audit).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"

# CLAUDE.md's niche is PC gaming/hardware -- "20" is YouTube's own fixed
# "Gaming" video category id, not something invented per video.
DEFAULT_CATEGORY_ID = "20"
DEFAULT_PRIVACY_STATUS = "private"
DEFAULT_MADE_FOR_KIDS = False

# 8 MiB chunks: comfortably larger than one HTTP round trip needs to be
# efficient for a short-form (tens of MB) video, small enough that a
# single failed chunk (see next_chunk's own num_retries) never has to
# re-send the whole file.
UPLOAD_CHUNK_SIZE_BYTES = 8 * 1024 * 1024
UPLOAD_CHUNK_RETRIES = 3


class YouTubeConfigError(RuntimeError):
    """Raised when required OAuth configuration is missing or the refresh
    token could not be exchanged for an access token -- a configuration/
    auth problem, distinct from an upload-call failure (see
    YouTubeUploadError). Never constructed with a raw secret value."""


class YouTubeUploadError(RuntimeError):
    """Raised when the authenticated videos.insert call itself fails (bad
    file, quota, API error, network). Always constructed via
    _safe_error_message() below -- never from an exception's raw str(),
    which for HTTP-layer errors can include request details."""


@dataclass
class YouTubeUploadResult:
    video_id: str
    youtube_url: str
    privacy_status: str


def _safe_error_message(exc: Exception) -> str:
    """A short, credential-safe description of a YouTube API failure --
    mirrors preflight.py's own `_safe_error_message` pattern. `HttpError`'s
    `.resp.status`/`.reason` come from the parsed JSON *response* body
    (Google's own documented error fields), never from the *request*
    (where the bearer access token / Authorization header lives), so
    these are always safe to include verbatim. Everything else falls back
    to just the exception's type name -- never str(exc), which for
    lower-level transport/auth exceptions can include request details.
    """
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", "unknown")
        reason = (getattr(exc, "reason", "") or "").strip() or "no further detail"
        return f"HTTP {status}: {reason}"
    return type(exc).__name__


class YouTubeProvider:
    """Uploads one finished MP4 to YouTube via videos.insert."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
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
            raise YouTubeConfigError(f"{', '.join(missing)} not set")

        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=[UPLOAD_SCOPE],
        )
        # Refreshed eagerly here (not lazily on first API call) so an
        # invalid/revoked refresh token surfaces as a clear
        # YouTubeConfigError right away, not as a confusing failure deep
        # inside a resumable upload's first chunk.
        try:
            credentials.refresh(Request())
        except Exception as exc:  # noqa: BLE001 -- google-auth's refresh-flow exceptions vary; sanitize uniformly
            raise YouTubeConfigError(f"Could not refresh YouTube OAuth credentials ({_safe_error_message(exc)})") from None

        self._client = build(API_SERVICE_NAME, API_VERSION, credentials=credentials, cache_discovery=False)

    def upload_video(
        self,
        video_path: Path,
        *,
        title: str,
        description: str,
        category_id: str = DEFAULT_CATEGORY_ID,
        privacy_status: str = DEFAULT_PRIVACY_STATUS,
        tags: Optional[list[str]] = None,
        made_for_kids: bool = DEFAULT_MADE_FOR_KIDS,
    ) -> YouTubeUploadResult:
        """Uploads `video_path` via `videos.insert(part="snippet,status")`
        using a resumable media upload. Raises YouTubeUploadError on any
        failure -- callers must never treat a partial/failed call as a
        successful publish.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise YouTubeUploadError(f"Video file does not exist: {video_path}")

        snippet: dict[str, Any] = {"title": title, "description": description, "categoryId": category_id}
        if tags:
            snippet["tags"] = tags

        body = {
            "snippet": snippet,
            "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": made_for_kids},
        }
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=UPLOAD_CHUNK_SIZE_BYTES, resumable=True)
        request = self._client.videos().insert(part="snippet,status", body=body, media_body=media)

        try:
            response = None
            while response is None:
                status, response = request.next_chunk(num_retries=UPLOAD_CHUNK_RETRIES)
                if status:
                    logger.info("YouTube upload progress: %d%%", int(status.progress() * 100))
        except Exception as exc:  # noqa: BLE001 -- HttpError and lower-level transport errors both sanitize the same way
            raise YouTubeUploadError(f"YouTube upload failed ({_safe_error_message(exc)})") from None

        video_id = response.get("id") if isinstance(response, dict) else None
        if not video_id:
            raise YouTubeUploadError("YouTube upload response did not include a video id")

        result_privacy = response.get("status", {}).get("privacyStatus", privacy_status)
        logger.info("YouTube upload succeeded: video_id=%s privacy=%s", video_id, result_privacy)
        return YouTubeUploadResult(
            video_id=video_id,
            youtube_url=f"https://www.youtube.com/watch?v={video_id}",
            privacy_status=result_privacy,
        )
