import json

from scripts.research.sources.fixture_source import FixtureResearchSource


def test_fixture_source_loads_configured_fixture_file():
    from scripts.research.cli import DEFAULT_CONFIG_PATH
    from scripts.config import BASE_DIR

    config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    fixture_entry = next(entry for entry in config["sources"] if entry["type"] == "fixture")
    source = FixtureResearchSource(name=fixture_entry["name"], path=BASE_DIR / fixture_entry["path"])

    candidates = source.fetch()

    assert len(candidates) >= 5
    assert all(c.source_name == fixture_entry["name"] for c in candidates)


def test_fixture_source_is_deterministic_across_calls():
    from scripts.research.cli import DEFAULT_CONFIG_PATH
    from scripts.config import BASE_DIR

    config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    fixture_entry = next(entry for entry in config["sources"] if entry["type"] == "fixture")
    source = FixtureResearchSource(name=fixture_entry["name"], path=BASE_DIR / fixture_entry["path"])

    first = [c.candidate_id for c in source.fetch()]
    second = [c.candidate_id for c in source.fetch()]

    assert first == second


def test_fixture_source_skips_malformed_items(tmp_path, caplog):
    fixture_path = tmp_path / "candidates.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "title": "Valid candidate",
                    "content_pillar": "buying_advice",
                    "content_role": "growth",
                },
                {
                    "title": "Malformed candidate",
                    "content_pillar": "not_a_real_pillar",
                    "content_role": "growth",
                },
            ]
        ),
        encoding="utf-8",
    )
    source = FixtureResearchSource(name="fixture_test", path=fixture_path)

    with caplog.at_level("WARNING"):
        candidates = source.fetch()

    assert len(candidates) == 1
    assert candidates[0].title == "Valid candidate"
    assert "Malformed" not in "".join(c.title for c in candidates)
    assert any("malformed" in record.message.lower() for record in caplog.records)


def test_fixture_source_raises_for_missing_file(tmp_path):
    import pytest

    source = FixtureResearchSource(name="fixture_test", path=tmp_path / "does_not_exist.json")
    with pytest.raises(FileNotFoundError):
        source.fetch()
