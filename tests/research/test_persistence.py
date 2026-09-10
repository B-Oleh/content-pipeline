from datetime import datetime, timezone

from scripts.research.models import (
    ContentPillar,
    ContentRole,
    ResearchCandidate,
    ResearchResult,
    ScoreBreakdown,
    ScoredCandidate,
    SourceHealth,
)
from scripts.research.persistence import (
    RESULTS_FILENAME,
    SUMMARY_FILENAME,
    load_research_results,
    render_summary_markdown,
    save_research_results,
)


def _score_kwargs(**overrides):
    kwargs = {
        "audience_interest": 7.0,
        "commercial_intent": 6.0,
        "affiliate_potential": 5.0,
        "competition": 5.0,
        "novelty": 4.0,
        "visual_potential": 6.0,
        "retention_potential": 6.0,
        "production_difficulty": 5.0,
        "confidence": 6.0,
        "freshness": 5.0,
        "evergreen_value": 6.0,
    }
    kwargs.update(overrides)
    return kwargs


def _sample_result() -> ResearchResult:
    candidate = ResearchCandidate(
        candidate_id="c1",
        title="Best budget GPUs under $200",
        content_pillar=ContentPillar.BUYING_ADVICE,
        content_role=ContentRole.HYBRID,
        source_name="fixture_test",
        monetization_path="GPU affiliate links",
    )
    scores = ScoreBreakdown(**_score_kwargs())
    scored = ScoredCandidate(
        candidate=candidate,
        scores=scores,
        overall_score=6.4,
        rank=1,
        reasoning=["audience_interest scored 7.0/10"],
        freshness_tier="recent",
    )
    return ResearchResult(
        generated_at=datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc),
        ranking_formula_version="v0.1",
        candidates=[scored],
        source_errors=["broken_source: connection refused"],
        source_health=[
            SourceHealth(
                source_name="fixture_test",
                success=True,
                item_count=1,
                duration_seconds=0.05,
                retrieved_at="2026-03-05T12:00:00+00:00",
            ),
            SourceHealth(
                source_name="broken_source",
                success=False,
                item_count=0,
                duration_seconds=1.2,
                retrieved_at="2026-03-05T12:00:00+00:00",
                error_category="network",
                error_message="connection refused",
            ),
        ],
        raw_candidate_count=2,
        deduplicated_candidate_count=1,
    )


def test_save_research_results_writes_dated_directory(tmp_path):
    result = _sample_result()
    json_path, md_path = save_research_results(result, base_dir=tmp_path)

    assert json_path == tmp_path / "2026-03-05" / RESULTS_FILENAME
    assert md_path == tmp_path / "2026-03-05" / SUMMARY_FILENAME
    assert json_path.exists()
    assert md_path.exists()


def test_save_and_load_round_trip(tmp_path):
    result = _sample_result()
    json_path, _ = save_research_results(result, base_dir=tmp_path)

    loaded = load_research_results(json_path)

    assert loaded.ranking_formula_version == result.ranking_formula_version
    assert loaded.source_errors == result.source_errors
    assert len(loaded.candidates) == 1
    assert loaded.candidates[0].candidate.title == "Best budget GPUs under $200"
    assert loaded.candidates[0].freshness_tier == "recent"
    assert loaded.raw_candidate_count == 2
    assert loaded.deduplicated_candidate_count == 1
    assert len(loaded.source_health) == 2
    assert loaded.source_health[1].error_category == "network"


def test_summary_markdown_includes_key_fields():
    result = _sample_result()
    markdown = render_summary_markdown(result)

    assert "Best budget GPUs under $200" in markdown
    assert "buying_advice" in markdown
    assert "hybrid" in markdown
    assert "GPU affiliate links" in markdown
    assert "broken_source: connection refused" in markdown
    assert "heuristic" in markdown


def test_summary_markdown_includes_source_health_and_degraded_warning():
    result = _sample_result()
    markdown = render_summary_markdown(result)

    assert "Source health" in markdown
    assert "fixture_test" in markdown
    assert "FAILED (network)" in markdown
    assert "WARNING: research quality is degraded" in markdown
    assert "Raw candidates collected: 2" in markdown
    assert "Candidates after deduplication: 1" in markdown


def test_summary_markdown_omits_degraded_warning_when_healthy():
    result = _sample_result()
    result.source_health = [
        SourceHealth(
            source_name="fixture_test",
            success=True,
            item_count=1,
            duration_seconds=0.05,
            retrieved_at="2026-03-05T12:00:00+00:00",
        )
    ]
    markdown = render_summary_markdown(result)
    assert "WARNING: research quality is degraded" not in markdown
