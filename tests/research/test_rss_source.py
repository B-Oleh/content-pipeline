import xml.etree.ElementTree as ET

import pytest

from scripts.research.models import ContentPillar, ContentRole
from scripts.research.sources.rss_source import RssResearchSource

EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Feed</title>
  </channel>
</rss>
"""

MALFORMED_FEED = b"<rss version=\"2.0\"><channel><item><title>Broken</channel>"

SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sample Feed</title>
    <item>
      <title>New GPU driver improves stability</title>
      <link>https://example.com/gpu-driver</link>
      <description>A short summary.</description>
      <pubDate>Mon, 02 Mar 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>  </title>
      <link>https://example.com/empty-title</link>
    </item>
    <item>
      <title>Another gaming technology headline</title>
      <link>https://example.com/other</link>
    </item>
  </channel>
</rss>
"""


def _fake_fetcher(url: str, timeout: float) -> bytes:
    return SAMPLE_FEED


def test_rss_source_parses_items_and_skips_blank_titles():
    source = RssResearchSource(
        name="test_rss",
        feed_url="https://example.com/rss",
        default_content_pillar=ContentPillar.GAMING_TECHNOLOGY,
        default_content_role=ContentRole.GROWTH,
        fetcher=_fake_fetcher,
    )

    candidates = source.fetch()

    assert len(candidates) == 2
    assert candidates[0].title == "New GPU driver improves stability"
    assert candidates[0].source_url == "https://example.com/gpu-driver"
    assert candidates[0].content_pillar == ContentPillar.GAMING_TECHNOLOGY
    assert candidates[0].content_role == ContentRole.GROWTH
    assert candidates[0].raw_metadata["published_at"] is not None
    assert candidates[0].raw_metadata["retrieved_at"] is not None


def test_rss_source_respects_max_items():
    source = RssResearchSource(
        name="test_rss",
        feed_url="https://example.com/rss",
        default_content_pillar=ContentPillar.GAMING_TECHNOLOGY,
        default_content_role=ContentRole.GROWTH,
        max_items=1,
        fetcher=_fake_fetcher,
    )

    candidates = source.fetch()

    assert len(candidates) == 1


def test_rss_source_propagates_fetcher_errors():
    def _failing_fetcher(url: str, timeout: float) -> bytes:
        raise TimeoutError("feed unreachable")

    source = RssResearchSource(
        name="test_rss",
        feed_url="https://example.com/rss",
        default_content_pillar=ContentPillar.GAMING_TECHNOLOGY,
        default_content_role=ContentRole.GROWTH,
        fetcher=_failing_fetcher,
    )

    with pytest.raises(TimeoutError):
        source.fetch()


def test_rss_source_candidate_ids_are_deterministic():
    source = RssResearchSource(
        name="test_rss",
        feed_url="https://example.com/rss",
        default_content_pillar=ContentPillar.GAMING_TECHNOLOGY,
        default_content_role=ContentRole.GROWTH,
        fetcher=_fake_fetcher,
    )

    first = [c.candidate_id for c in source.fetch()]
    second = [c.candidate_id for c in source.fetch()]

    assert first == second


def test_rss_source_handles_empty_feed():
    source = RssResearchSource(
        name="test_rss",
        feed_url="https://example.com/rss",
        default_content_pillar=ContentPillar.GAMING_TECHNOLOGY,
        default_content_role=ContentRole.GROWTH,
        fetcher=lambda url, timeout: EMPTY_FEED,
    )

    assert source.fetch() == []


def test_rss_source_raises_parse_error_for_malformed_xml():
    source = RssResearchSource(
        name="test_rss",
        feed_url="https://example.com/rss",
        default_content_pillar=ContentPillar.GAMING_TECHNOLOGY,
        default_content_role=ContentRole.GROWTH,
        fetcher=lambda url, timeout: MALFORMED_FEED,
    )

    with pytest.raises(ET.ParseError):
        source.fetch()


def test_rss_source_sends_a_descriptive_user_agent(monkeypatch):
    import urllib.request

    captured_requests = []

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return EMPTY_FEED

    def _capturing_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _capturing_urlopen)

    source = RssResearchSource(
        name="test_rss",
        feed_url="https://example.com/rss",
        default_content_pillar=ContentPillar.GAMING_TECHNOLOGY,
        default_content_role=ContentRole.GROWTH,
    )
    source.fetch()

    assert len(captured_requests) == 1
    assert "User-Agent" in captured_requests[0].headers or "User-agent" in captured_requests[0].headers
