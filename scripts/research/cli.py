"""Orchestration and CLI for Research Agent.

Run locally with:

    python -m scripts.research_agent

See docs/RESEARCH_AGENT.md for the full stage design.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from scripts.config import BASE_DIR, DATA_DIR, STATE_DIR
from scripts.research.dedup import deduplicate_candidates
from scripts.research.game_history import load_game_history, record_game_recommendations
from scripts.research.models import ContentPillar, ContentRole, ResearchCandidate, ResearchResult, SourceHealth
from scripts.research.persistence import save_research_results
from scripts.research.ranking import RANKING_FORMULA_VERSION, rank_candidates
from scripts.research.scoring import score_candidate
from scripts.research.source_health import classify_error
from scripts.research.sources.base import ResearchSource
from scripts.research.sources.fixture_source import FixtureResearchSource
from scripts.research.sources.rss_source import RssResearchSource
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "sources.json"
# Transient per-run output (see docs/RESEARCH_AGENT.md "Persistent state vs
# transient output"): a new dated subfolder every run under the gitignored
# data/ directory, safe to discard -- never committed.
DEFAULT_OUTPUT_BASE_DIR = DATA_DIR / "research"
# Persistent pipeline state that must survive across runs (currently just
# game_history.json). Since V0.3, this lives under the tracked (NOT
# gitignored) state/ directory rather than under data/, specifically so it
# survives across separate GitHub Actions runs on ephemeral runners -- see
# docs/RESEARCH_AGENT.md "Git-backed persistent state" for why Git is used
# as the $0 MVP durability mechanism and how this could later be replaced.
DEFAULT_STATE_DIR = STATE_DIR / "research"


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


def collect_candidates(sources: list[ResearchSource]) -> tuple[list[ResearchCandidate], list[SourceHealth]]:
    """Fetch from every source; a failing source is logged and skipped, not fatal.

    Returns the raw (not yet deduplicated/scored) candidates plus one
    SourceHealth record per source (see docs/RESEARCH_AGENT.md "Source
    health") so a degraded run is visible in the output, not just the logs.
    """
    candidates: list[ResearchCandidate] = []
    health_reports: list[SourceHealth] = []

    for source in sources:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        try:
            fetched = source.fetch()
        except Exception as exc:  # noqa: BLE001 -- deliberate per-source isolation boundary
            duration = round(time.monotonic() - started, 3)
            category = classify_error(exc)
            logger.error("Research source %s failed (%s) after %.2fs: %s", source.name, category, duration, exc)
            health_reports.append(
                SourceHealth(
                    source_name=source.name,
                    success=False,
                    item_count=0,
                    duration_seconds=duration,
                    retrieved_at=retrieved_at,
                    error_category=category,
                    error_message=str(exc),
                )
            )
            continue

        duration = round(time.monotonic() - started, 3)
        logger.info("Research source %s returned %d candidate(s) in %.2fs", source.name, len(fetched), duration)
        health_reports.append(
            SourceHealth(
                source_name=source.name,
                success=True,
                item_count=len(fetched),
                duration_seconds=duration,
                retrieved_at=retrieved_at,
            )
        )
        candidates.extend(fetched)

    return candidates, health_reports


def run(
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_dir: Path = DEFAULT_STATE_DIR,
) -> ResearchResult:
    as_of = datetime.now(timezone.utc)
    logger.info("Research Agent starting (config: %s, state: %s)", config_path, state_dir)

    sources = build_sources(config_path)
    raw_candidates, health_reports = collect_candidates(sources)
    source_errors = [f"{health.source_name}: {health.error_message}" for health in health_reports if not health.success]

    deduplicated = deduplicate_candidates(raw_candidates)
    if len(deduplicated) < len(raw_candidates):
        logger.info(
            "Deduplication merged %d raw candidate(s) into %d",
            len(raw_candidates),
            len(deduplicated),
        )

    game_history = load_game_history(state_dir)

    scored_pairs = [(candidate, score_candidate(candidate)) for candidate in deduplicated]
    ranked = rank_candidates(scored_pairs, game_history=game_history, as_of=as_of)

    result = ResearchResult(
        generated_at=as_of,
        ranking_formula_version=RANKING_FORMULA_VERSION,
        candidates=ranked,
        source_errors=source_errors,
        source_health=health_reports,
        raw_candidate_count=len(raw_candidates),
        deduplicated_candidate_count=len(deduplicated),
    )
    logger.info("Research Agent scored and ranked %d candidate(s)", len(ranked))
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run Research Agent locally.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to sources.json")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_BASE_DIR,
        help="Base directory for dated, transient per-run output (default: data/research)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Directory for persistent pipeline state, e.g. game history (default: state/research)",
    )
    args = parser.parse_args(argv)

    result = run(config_path=args.config, state_dir=args.state_dir)
    json_path, md_path = save_research_results(result, base_dir=args.output_dir)
    record_game_recommendations(result, base_dir=args.state_dir)

    logger.info("Saved results to %s", json_path)
    logger.info("Saved summary to %s", md_path)
    print(f"Results: {json_path}")
    print(f"Summary: {md_path}")


if __name__ == "__main__":
    main()
