"""Orchestration and CLI for Research Agent V0.1.

Run locally with:

    python -m scripts.research_agent

See docs/RESEARCH_AGENT.md for the full stage design.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from scripts.config import BASE_DIR, DATA_DIR
from scripts.research.game_history import record_game_recommendations
from scripts.research.models import ContentPillar, ContentRole, ResearchCandidate, ResearchResult
from scripts.research.persistence import save_research_results
from scripts.research.ranking import RANKING_FORMULA_VERSION, rank_candidates
from scripts.research.scoring import score_candidate
from scripts.research.sources.base import ResearchSource
from scripts.research.sources.fixture_source import FixtureResearchSource
from scripts.research.sources.rss_source import RssResearchSource
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "sources.json"
DEFAULT_OUTPUT_BASE_DIR = DATA_DIR / "research"


def _build_fixture_source(entry: dict) -> ResearchSource:
    return FixtureResearchSource(name=entry["name"], path=BASE_DIR / entry["path"])


def _build_rss_source(entry: dict) -> ResearchSource:
    return RssResearchSource(
        name=entry["name"],
        feed_url=entry["feed_url"],
        default_content_pillar=ContentPillar(entry["default_content_pillar"]),
        default_content_role=ContentRole(entry["default_content_role"]),
        max_items=entry.get("max_items", 15),
        timeout=entry.get("timeout", 10.0),
    )


SOURCE_BUILDERS: dict[str, Callable[[dict], ResearchSource]] = {
    "fixture": _build_fixture_source,
    "rss": _build_rss_source,
}


def build_sources(config_path: Path) -> list[ResearchSource]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    sources: list[ResearchSource] = []
    for entry in config.get("sources", []):
        if not entry.get("enabled", True):
            continue
        builder = SOURCE_BUILDERS.get(entry.get("type"))
        if builder is None:
            logger.error("Unknown research source type %r in %s; skipping", entry.get("type"), config_path)
            continue
        try:
            sources.append(builder(entry))
        except (KeyError, ValueError) as exc:
            logger.error("Failed to build research source from entry %s: %s", entry, exc)
    return sources


def collect_candidates(sources: list[ResearchSource]) -> tuple[list[ResearchCandidate], list[str]]:
    """Fetch from every source; a failing source is logged and skipped, not fatal."""
    candidates: list[ResearchCandidate] = []
    errors: list[str] = []
    for source in sources:
        try:
            fetched = source.fetch()
        except Exception as exc:  # noqa: BLE001 -- any source failure must not abort the run
            logger.error("Research source %s failed: %s", source.name, exc)
            errors.append(f"{source.name}: {exc}")
            continue
        logger.info("Research source %s returned %d candidate(s)", source.name, len(fetched))
        candidates.extend(fetched)
    return candidates, errors


def run(config_path: Path = DEFAULT_CONFIG_PATH) -> ResearchResult:
    logger.info("Research Agent V0.1 starting (config: %s)", config_path)
    sources = build_sources(config_path)
    candidates, source_errors = collect_candidates(sources)

    scored_pairs = [(candidate, score_candidate(candidate)) for candidate in candidates]
    ranked = rank_candidates(scored_pairs)

    result = ResearchResult(
        generated_at=datetime.now(timezone.utc),
        ranking_formula_version=RANKING_FORMULA_VERSION,
        candidates=ranked,
        source_errors=source_errors,
    )
    logger.info("Research Agent V0.1 scored and ranked %d candidate(s)", len(ranked))
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run Research Agent V0.1 locally.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to sources.json")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_BASE_DIR,
        help="Base directory for dated output subfolders (default: data/research)",
    )
    args = parser.parse_args(argv)

    result = run(config_path=args.config)
    json_path, md_path = save_research_results(result, base_dir=args.output_dir)
    record_game_recommendations(result, base_dir=args.output_dir)

    logger.info("Saved results to %s", json_path)
    logger.info("Saved summary to %s", md_path)
    print(f"Results: {json_path}")
    print(f"Summary: {md_path}")


if __name__ == "__main__":
    main()
