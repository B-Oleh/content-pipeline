"""Conservative per-run duplicate detection for freshly collected candidates.

Real sources frequently report the same story. This module merges obvious
duplicates -- an identical (normalized) source URL, or an identical
(normalized) title -- so ranking never sees the same opportunity twice. It
deliberately does NOT do fuzzy/semantic matching: two headlines that merely
share common gaming words (e.g. "GPU", "RTX", "best") are never merged, only
an exact match after conservative normalization.

Merging preserves provenance: the kept candidate's raw_metadata["evidence"]
list gains one entry per merged-away duplicate, so a future Fact Checker can
see every source that reported the same story (see docs/RESEARCH_AGENT.md
"Evidence").
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from scripts.research.models import ResearchCandidate
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace for exact-match dedup."""
    lowered = title.strip().lower()
    without_punctuation = _PUNCTUATION_RE.sub("", lowered)
    return _WHITESPACE_RE.sub(" ", without_punctuation).strip()


def normalize_url(url: Optional[str]) -> Optional[str]:
    """Normalize a URL for exact-identity comparison (scheme/host/path only).

    Query strings and fragments are dropped -- tracking parameters commonly
    differ across shares of the same underlying story, so they are not part
    of "the same URL" for dedup purposes.
    """
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None
    normalized_path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), normalized_path, "", ""))


def _evidence_entry(candidate: ResearchCandidate) -> dict[str, Any]:
    raw_metadata = candidate.raw_metadata or {}
    return {
        "source_name": candidate.source_name,
        "source_url": candidate.source_url,
        "title": candidate.title,
        "summary": candidate.summary,
        "published_at": raw_metadata.get("published_at"),
        "retrieved_at": raw_metadata.get("retrieved_at"),
    }


def deduplicate_candidates(candidates: list[ResearchCandidate]) -> list[ResearchCandidate]:
    """Merge candidates that are obviously the same story from multiple sources.

    Returns a new list containing the first-seen candidate for each distinct
    URL/title identity; merged-away duplicates contribute an evidence entry
    to the kept candidate's raw_metadata["evidence"] instead of being
    silently dropped.
    """
    by_url: dict[str, ResearchCandidate] = {}
    by_title: dict[str, ResearchCandidate] = {}
    kept: list[ResearchCandidate] = []

    for candidate in candidates:
        url_key = normalize_url(candidate.source_url)
        title_key = normalize_title(candidate.title)

        primary = by_url.get(url_key) if url_key else None
        if primary is None:
            primary = by_title.get(title_key)

        if primary is None:
            candidate.raw_metadata.setdefault("evidence", [_evidence_entry(candidate)])
            kept.append(candidate)
            if url_key:
                by_url[url_key] = candidate
            by_title[title_key] = candidate
            continue

        logger.info(
            "Merging duplicate candidate %r (source %s) into %r (source %s)",
            candidate.title,
            candidate.source_name,
            primary.title,
            primary.source_name,
        )
        evidence = primary.raw_metadata.setdefault("evidence", [_evidence_entry(primary)])
        evidence.append(_evidence_entry(candidate))

    return kept
