from scripts.research.cli import build_sources, collect_candidates, DEFAULT_CONFIG_PATH
from scripts.research.models import ContentPillar, ContentRole, ResearchCandidate
from scripts.research.sources.base import ResearchSource


class _WorkingSource(ResearchSource):
    def __init__(self, name: str, candidate: ResearchCandidate) -> None:
        self.name = name
        self._candidate = candidate

    def fetch(self) -> list[ResearchCandidate]:
        return [self._candidate]


class _FailingSource(ResearchSource):
    def __init__(self, name: str) -> None:
        self.name = name

    def fetch(self) -> list[ResearchCandidate]:
        raise RuntimeError("source unavailable")


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
    working = _WorkingSource("working_source", _candidate("ok"))
    failing = _FailingSource("failing_source")

    candidates, errors = collect_candidates([failing, working])

    assert [c.candidate_id for c in candidates] == ["ok"]
    assert len(errors) == 1
    assert "failing_source" in errors[0]
