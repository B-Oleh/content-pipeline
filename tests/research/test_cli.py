from scripts.config import DATA_DIR, STATE_DIR
from scripts.research.cli import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_OUTPUT_BASE_DIR,
    DEFAULT_STATE_DIR,
    build_sources,
    collect_candidates,
    run,
)
from scripts.research.models import ContentPillar, ContentRole, ResearchCandidate
from scripts.research.sources.base import ResearchSource


class _WorkingSource(ResearchSource):
    def __init__(self, name: str, candidates: list[ResearchCandidate]) -> None:
        self.name = name
        self._candidates = candidates

    def fetch(self) -> list[ResearchCandidate]:
        return self._candidates


class _FailingSource(ResearchSource):
    def __init__(self, name: str, exc: Exception) -> None:
        self.name = name
        self._exc = exc

    def fetch(self) -> list[ResearchCandidate]:
        raise self._exc


def _candidate(candidate_id: str) -> ResearchCandidate:
    return ResearchCandidate(
        candidate_id=candidate_id,
        title=f"Topic {candidate_id}",
        content_pillar=ContentPillar.BUYING_ADVICE,
        content_role=ContentRole.GROWTH,
        source_name="test",
    )


def test_build_sources_loads_default_config():
    sources = build_sources(DEFAULT_CONFIG_PATH)
    assert len(sources) >= 1
    assert any(source.name == "fixture_sample_v1" for source in sources)


def test_build_sources_skips_unknown_type(tmp_path):
    config_path = tmp_path / "sources.json"
    config_path.write_text(
        '{"sources": [{"type": "unknown_type", "name": "x", "enabled": true}]}',
        encoding="utf-8",
    )
    sources = build_sources(config_path)
    assert sources == []


def test_collect_candidates_continues_after_a_source_fails():
    working = _WorkingSource("working_source", [_candidate("ok")])
    failing = _FailingSource("failing_source", RuntimeError("source unavailable"))

    candidates, health_reports = collect_candidates([failing, working])

    assert [c.candidate_id for c in candidates] == ["ok"]
    assert {h.source_name: h.success for h in health_reports} == {
        "failing_source": False,
        "working_source": True,
    }
    failing_report = next(h for h in health_reports if h.source_name == "failing_source")
    assert failing_report.error_category == "unknown"
    assert "source unavailable" in failing_report.error_message


def test_collect_candidates_reports_timeout_category():
    failing = _FailingSource("slow_source", TimeoutError("timed out"))

    _, health_reports = collect_candidates([failing])

    assert health_reports[0].error_category == "timeout"
    assert health_reports[0].success is False


def test_collect_candidates_handles_empty_source_results():
    empty = _WorkingSource("empty_source", [])

    candidates, health_reports = collect_candidates([empty])

    assert candidates == []
    assert health_reports[0].success is True
    assert health_reports[0].item_count == 0


def test_run_continues_and_reports_degraded_when_all_sources_fail(tmp_path, monkeypatch):
    failing = _FailingSource("failing_source", RuntimeError("boom"))
    monkeypatch.setattr("scripts.research.cli.build_sources", lambda config_path: [failing])

    result = run(config_path=DEFAULT_CONFIG_PATH, state_dir=tmp_path)

    assert result.candidates == []
    assert result.raw_candidate_count == 0
    assert result.deduplicated_candidate_count == 0
    assert len(result.source_health) == 1
    assert result.source_health[0].success is False
    assert "failing_source" in result.source_errors[0]


def test_run_uses_state_dir_for_game_history(tmp_path, monkeypatch):
    game_candidate = ResearchCandidate(
        candidate_id="g1",
        title="What to play this month on a low-end PC",
        content_pillar=ContentPillar.BUYING_ADVICE,
        content_role=ContentRole.GROWTH,
        source_name="test",
    )
    working = _WorkingSource("working_source", [game_candidate])
    monkeypatch.setattr("scripts.research.cli.build_sources", lambda config_path: [working])

    result = run(config_path=DEFAULT_CONFIG_PATH, state_dir=tmp_path)

    assert result.raw_candidate_count == 1
    assert result.deduplicated_candidate_count == 1
    assert len(result.candidates) == 1


# --- V0.3: persistent state moved out of data/ into state/ -------------------


def test_default_state_dir_is_under_the_tracked_state_directory():
    """State path migration guard: the default must live under the git-tracked
    state/ directory (see scripts/config.py::STATE_DIR), not under the
    gitignored data/ directory where V0.2 kept it -- otherwise persistent
    state would silently stop surviving GitHub Actions runs again."""
    assert DEFAULT_STATE_DIR == STATE_DIR / "research"
    assert STATE_DIR in DEFAULT_STATE_DIR.parents


def test_default_state_dir_is_not_under_data_dir():
    """Regression guard for the V0.2 layout (data/research/state/) -- must
    not come back, since data/ is gitignored and would not survive CI."""
    assert DATA_DIR not in DEFAULT_STATE_DIR.parents
    assert DATA_DIR != DEFAULT_STATE_DIR


def test_default_output_dir_and_default_state_dir_are_disjoint_trees():
    """No accidental transient-state persistence: the transient output tree
    (data/research/) and the persistent state tree (state/research/) must
    never be nested inside one another, so writing one can never leak into
    or be confused with the other."""
    assert DEFAULT_OUTPUT_BASE_DIR not in DEFAULT_STATE_DIR.parents
    assert DEFAULT_STATE_DIR not in DEFAULT_OUTPUT_BASE_DIR.parents
    assert DEFAULT_OUTPUT_BASE_DIR != DEFAULT_STATE_DIR


def test_default_paths_are_derived_from_base_dir_not_hard_coded():
    # Both defaults must be computed from scripts.config's BASE_DIR-relative
    # constants (which resolve wherever the repo happens to be checked out),
    # never a literal absolute path baked into the source -- so the same
    # code behaves identically on any machine/runner.
    from scripts.config import BASE_DIR

    assert str(BASE_DIR) in str(DEFAULT_STATE_DIR)
    assert str(BASE_DIR) in str(DEFAULT_OUTPUT_BASE_DIR)
