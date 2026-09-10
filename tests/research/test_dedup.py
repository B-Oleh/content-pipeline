from scripts.research.dedup import deduplicate_candidates, normalize_title, normalize_url
from scripts.research.models import ContentPillar, ContentRole, ResearchCandidate


def _candidate(candidate_id: str, title: str, source_name: str, source_url: str | None = None) -> ResearchCandidate:
    return ResearchCandidate(
        candidate_id=candidate_id,
        title=title,
        content_pillar=ContentPillar.GAMING_TECHNOLOGY,
        content_role=ContentRole.GROWTH,
        source_name=source_name,
        source_url=source_url,
    )


def test_normalize_title_collapses_case_punctuation_and_whitespace():
    assert normalize_title("  New GPU Driver: Improves Stability!  ") == "new gpu driver improves stability"
    assert normalize_title("New GPU Driver Improves Stability") == "new gpu driver improves stability"


def test_normalize_url_drops_query_and_fragment_and_trailing_slash():
    a = normalize_url("https://example.com/news/gpu-driver/?utm_source=rss")
    b = normalize_url("https://example.com/news/gpu-driver#comments")
    c = normalize_url("HTTPS://Example.com/news/gpu-driver/")
    assert a == b == c


def test_normalize_url_none_for_missing_or_blank():
    assert normalize_url(None) is None
    assert normalize_url("   ") is None


def test_deduplicate_merges_exact_url_match():
    first = _candidate("a", "New GPU driver improves stability", "source_a", "https://example.com/story")
    second = _candidate("b", "GPU Driver Update Ships Today", "source_b", "https://example.com/story?utm=1")

    result = deduplicate_candidates([first, second])

    assert len(result) == 1
    evidence = result[0].raw_metadata["evidence"]
    assert {entry["source_name"] for entry in evidence} == {"source_a", "source_b"}


def test_deduplicate_merges_exact_normalized_title_match():
    first = _candidate("a", "Best budget GPUs under $200", "source_a")
    second = _candidate("b", "  best budget gpus under $200!!  ", "source_b")

    result = deduplicate_candidates([first, second])

    assert len(result) == 1
    assert len(result[0].raw_metadata["evidence"]) == 2


def test_deduplicate_does_not_merge_titles_sharing_only_common_words():
    first = _candidate("a", "Best budget GPUs under $200", "source_a")
    second = _candidate("b", "Best budget gaming mice under $30", "source_b")

    result = deduplicate_candidates([first, second])

    assert len(result) == 2


def test_deduplicate_preserves_single_candidates_with_evidence_seeded():
    only = _candidate("a", "Unique topic", "source_a", "https://example.com/unique")

    result = deduplicate_candidates([only])

    assert len(result) == 1
    assert len(result[0].raw_metadata["evidence"]) == 1
    assert result[0].raw_metadata["evidence"][0]["source_name"] == "source_a"


def test_deduplicate_handles_empty_list():
    assert deduplicate_candidates([]) == []
