"""Idempotent YouTube-publication bookkeeping for the Telegram "Approve"
flow.

Deliberately contains NO Telegram-specific logic -- only content_id ->
publication-record state and the "check-existing, else upload, then
persist" orchestration around providers/youtube.py. See pipeline.py for
how a Telegram confirmation message is built from this module's return
value / exceptions.

State is a small transient JSON file under the run's own workdir (same
place as script.json/qa_result.json/approval_state.json -- gitignored, not
committed), keyed by content_id, so a second Approve callback for the
SAME content_id can detect the earlier successful upload and reuse its
video_id/URL instead of calling videos.insert again (see
publish_approved_video's docstring).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scripts.production.providers.youtube import DEFAULT_PRIVACY_STATUS, YouTubeProvider
from scripts.utils.atomic_write import atomic_write_text
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class PublicationRecord:
    content_id: str
    decision: str
    youtube_video_id: str
    youtube_url: str
    privacy_status: str
    uploaded_at: str  # ISO 8601 UTC timestamp

    def to_dict(self) -> dict:
        return asdict(self)


def _load_all_records(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read existing YouTube publication state at %s (%s) -- treating as empty", path, exc)
        return {}


def load_publication_record(path: Path, content_id: str) -> Optional[PublicationRecord]:
    """Returns the existing publication record for `content_id`, if one
    was already recorded (a prior successful upload) -- see
    publish_approved_video()."""
    data = _load_all_records(path).get(content_id)
    if data is None:
        return None
    return PublicationRecord(**data)


def save_publication_record(path: Path, record: PublicationRecord) -> None:
    """Merges `record` into the existing publication-state file (keyed by
    content_id), atomically -- never overwrites other content_ids'
    records."""
    records = _load_all_records(path)
    records[record.content_id] = record.to_dict()
    atomic_write_text(path, json.dumps(records, indent=2))


def publish_approved_video(
    provider: YouTubeProvider,
    video_path: Path,
    *,
    content_id: str,
    title: str,
    description: str,
    state_path: Path,
) -> PublicationRecord:
    """Uploads `video_path` to YouTube for `content_id`, UNLESS a
    successful upload for this exact content_id was already recorded, in
    which case the existing record is returned unchanged and
    `videos.insert` is never called again -- the task's explicit "if the
    user presses Approve twice for the same content_id, do not upload the
    same video twice" requirement.

    Raises whatever `provider.upload_video()` raises (YouTubeUploadError)
    on a genuine upload failure -- this function never persists a record
    for a failed upload, and never swallows the exception: the caller
    (pipeline.py) decides what to tell Telegram and whether to retry.
    """
    existing = load_publication_record(state_path, content_id)
    if existing is not None:
        logger.info(
            "content_id=%s already has a recorded YouTube upload (video_id=%s) -- skipping duplicate upload",
            content_id, existing.youtube_video_id,
        )
        return existing

    result = provider.upload_video(video_path, title=title, description=description, privacy_status=DEFAULT_PRIVACY_STATUS)

    record = PublicationRecord(
        content_id=content_id,
        decision="approve",
        youtube_video_id=result.video_id,
        youtube_url=result.youtube_url,
        privacy_status=result.privacy_status,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )
    save_publication_record(state_path, record)
    return record
