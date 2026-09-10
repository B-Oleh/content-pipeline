"""RSS-based research source.

Uses only the standard library (urllib + xml.etree) so it needs no API key,
no paid access, and no extra dependency. RSS items only carry a
headline/link/summary, so this source cannot infer commercial intent or
content pillar on its own -- the caller supplies sensible defaults per feed
via configuration (see scripts/research/config/sources.json).
"""

from __future__ import annotations

import hashlib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from scripts.research.models import ContentPillar, ContentRole, ResearchCandidate
from scripts.research.sources.base import ResearchSource
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

Fetcher = Callable[[str, float], bytes]

# Identifies this project to the servers whose public feeds it reads. Not a
# spoofed browser user agent -- some feed hosts otherwise reject or
# rate-limit the default urllib identifier.
_USER_AGENT = "content-pipeline-research-agent/0.2"


def _default_fetcher(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (public RSS feed)
        return response.read()


class RssResearchSource(ResearchSource):
    """Reads candidate topics from a public RSS feed."""

    def __init__(
        self,
        name: str,
        feed_url: str,
        default_content_pillar: ContentPillar,
        default_content_role: ContentRole,
        max_items: int = 15,
        timeout: float = 10.0,
        fetcher: Fetcher = _default_fetcher,
    ) -> None:
        self.name = name
        self.feed_url = feed_url
        self.default_content_pillar = default_content_pillar
        self.default_content_role = default_content_role
        self.max_items = max_items
        self.timeout = timeout
        self._fetcher = fetcher

    def fetch(self) -> list[ResearchCandidate]:
        raw = self._fetcher(self.feed_url, self.timeout)
        return self._parse(raw)

    def _parse(self, raw: bytes) -> list[ResearchCandidate]:
        root = ET.fromstring(raw)
        items = root.findall("./channel/item")
        retrieved_at = datetime.now(timezone.utc).isoformat()
        candidates: list[ResearchCandidate] = []
        for index, item in enumerate(items[: self.max_items]):
            title = _text(item.find("title"))
            if not title:
                continue
            link = _text(item.find("link"))
            summary = _text(item.find("description"))
            published_at = _parse_pubdate(_text(item.find("pubDate")))
            candidate_id = f"{self.name}:{hashlib.sha1(title.encode('utf-8')).hexdigest()[:10]}"
            candidates.append(
                ResearchCandidate(
                    candidate_id=candidate_id,
                    title=title,
                    content_pillar=self.default_content_pillar,
                    content_role=self.default_content_role,
                    source_name=self.name,
                    source_url=link,
                    summary=summary,
                    raw_metadata={
                        "published_at": published_at.isoformat() if published_at else None,
                        "retrieved_at": retrieved_at,
                    },
                )
            )
        return candidates


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
