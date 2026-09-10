"""Persistence for Research Agent V0.1 results.

Writes plain JSON (machine-readable, re-loadable) and a Markdown summary
(human-readable, for manual review) per run, under
data/research/YYYY-MM-DD/ (see CLAUDE.md "Architecture" -- plain data
between stages, not hidden shared state).
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.research.models import ResearchResult, ScoredCandidate
from scripts.research.source_health import is_degraded
from scripts.utils.atomic_write import atomic_write_text

RESULTS_FILENAME = "research_results.json"
SUMMARY_FILENAME = "summary.md"


def save_research_results(result: ResearchResult, base_dir: Path) -> tuple[Path, Path]:
    """Write research_results.json and summary.md under base_dir/YYYY-MM-DD/."""
    date_str = result.generated_at.strftime("%Y-%m-%d")
    out_dir = Path(base_dir) / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / RESULTS_FILENAME
    atomic_write_text(json_path, json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    md_path = out_dir / SUMMARY_FILENAME
    atomic_write_text(md_path, render_summary_markdown(result))

    return json_path, md_path


def load_research_results(json_path: Path) -> ResearchResult:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    return ResearchResult.from_dict(data)


def render_summary_markdown(result: ResearchResult, top_n: int = 10) -> str:
    lines: list[str] = []
    lines.append(f"# Research Agent results -- {result.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append(f"Ranking formula version: `{result.ranking_formula_version}`")
    lines.append(f"Raw candidates collected: {result.raw_candidate_count}")
    lines.append(f"Candidates after deduplication: {result.deduplicated_candidate_count}")
    lines.append(f"Total ranked candidates: {len(result.candidates)}")
    lines.append("")

    if result.source_health:
        lines.extend(_render_source_health_section(result))

    if result.source_errors:
        lines.append("## Source failures")
        lines.append("")
        for error in result.source_errors:
            lines.append(f"- {error}")
        lines.append("")

    lines.append(f"## Top {min(top_n, len(result.candidates))} candidates")
    lines.append("")

    for scored in result.candidates[:top_n]:
        lines.extend(_render_candidate_section(scored))

    return "\n".join(lines) + "\n"


def _render_source_health_section(result: ResearchResult) -> list[str]:
    lines = ["## Source health", ""]
    if is_degraded(result.source_health):
        successes = sum(1 for health in result.source_health if health.success)
        lines.append(
            f"**WARNING: research quality is degraded.** Only {successes}/{len(result.source_health)} "
            "configured source(s) succeeded in this run -- treat these results as less complete/diverse "
            "than a healthy run."
        )
        lines.append("")
    for health in result.source_health:
        status = "OK" if health.success else f"FAILED ({health.error_category})"
        lines.append(
            f"- **{health.source_name}:** {status} -- {health.item_count} item(s), "
            f"{health.duration_seconds:.2f}s, retrieved {health.retrieved_at}"
        )
        if not health.success and health.error_message:
            lines.append(f"  - error: {health.error_message}")
    lines.append("")
    return lines


def _render_candidate_section(scored: ScoredCandidate) -> list[str]:
    candidate = scored.candidate
    scores = scored.scores
    lines = [
        f"### {scored.rank}. {candidate.title}",
        "",
        f"- **Content pillar:** {candidate.content_pillar.value}",
        f"- **Content role:** {candidate.content_role.value}",
        f"- **Overall score:** {scored.overall_score:.2f} / 10",
        f"- **Monetization path:** {candidate.monetization_path or 'none identified'}",
        f"- **Confidence:** {scores.confidence:.1f} / 10 (heuristic: {'confidence' in scores.heuristic_dimensions})",
        f"- **Source:** {candidate.source_name}" + (f" ({candidate.source_url})" if candidate.source_url else ""),
    ]
    if scored.freshness_tier:
        lines.append(f"- **Freshness tier:** {scored.freshness_tier} (recency evidence only, not popularity)")
    if scored.repetition_penalty:
        lines.append(f"- **Repetition penalty:** -{scored.repetition_penalty:.1f}")
    if candidate.hardware_tier:
        lines.append(f"- **Hardware tier:** {candidate.hardware_tier.value}")
    if candidate.game_title:
        lines.append(f"- **Game:** {candidate.game_title}")
    if candidate.game_price_type:
        lines.append(f"- **Price type:** {candidate.game_price_type.value}")
    if candidate.release_relevance:
        lines.append(f"- **Release relevance:** {candidate.release_relevance.value}")

    lines.append("- **Score breakdown:**")
    for dimension in (
        "audience_interest",
        "commercial_intent",
        "affiliate_potential",
        "competition",
        "novelty",
        "visual_potential",
        "retention_potential",
        "production_difficulty",
        "confidence",
        "freshness",
        "evergreen_value",
    ):
        value = getattr(scores, dimension)
        marker = "heuristic" if dimension in scores.heuristic_dimensions else "real data"
        lines.append(f"  - {dimension}: {value:.1f}/10 ({marker})")

    lines.append("- **Why it may be worth producing:**")
    for reason in scored.reasoning:
        lines.append(f"  - {reason}")

    if candidate.summary:
        lines.append(f"- **Source summary:** {candidate.summary}")

    evidence = (candidate.raw_metadata or {}).get("evidence", [])
    if evidence:
        lines.append(f"- **Evidence ({len(evidence)} source item(s) -- why the system believes this is current):**")
        for entry in evidence:
            source_name = entry.get("source_name", "unknown")
            source_url = entry.get("source_url")
            lines.append(f"  - {source_name}" + (f" ({source_url})" if source_url else ""))

    lines.append("")
    return lines
