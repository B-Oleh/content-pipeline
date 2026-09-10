# Research Agent V0.1

First functional pipeline stage (see CLAUDE.md "Pipeline stages", stage 1). Collects candidate
gaming-content topics, normalizes them into a common structure, scores them, ranks them, and
persists the results. Does not select topics for production on its own — later stages (Fact
Checking, Script Agent, Telegram Approval; not yet implemented) still gate what actually gets made.

Engineering rules (secrets, dependencies, logging, testing, etc.) are defined once in CLAUDE.md and
are not repeated here.

## Module map

```
scripts/research_agent.py         CLI entry point (python -m scripts.research_agent)
scripts/research/
  models.py                        ResearchCandidate, ScoreBreakdown, ScoredCandidate, ResearchResult
  scoring.py                       Heuristic scoring engine
  ranking.py                       Weighted ranking formula
  persistence.py                   JSON + Markdown output under data/research/YYYY-MM-DD/
  game_history.py                  Persisted history of recommended games (for the monthly pillar)
  cli.py                           Orchestration: build sources -> fetch -> score -> rank -> save
  sources/
    base.py                        ResearchSource interface
    fixture_source.py              Deterministic static source (JSON fixture)
    rss_source.py                  RSS source (stdlib only, no API key)
  config/
    sources.json                   Which sources run, and their per-feed defaults
    fixture_candidates.json        Sample candidate data for the fixture source
```

## Data model

- **ResearchCandidate**: one raw content idea from a source, before scoring. Carries
  `content_pillar`, `content_role`, `monetization_path`, provenance (`source_name`/`source_url`),
  and optional fields for the monthly game pillar (`hardware_tier`, `target_gpu_class`,
  `game_price_type`, `release_relevance`, `game_title`).
- **ScoreBreakdown**: one 0-10 float per scoring dimension (see below), plus
  `heuristic_dimensions` — the set of dimensions whose value is a heuristic guess rather than real
  evidence.
- **ScoredCandidate**: a candidate plus its `ScoreBreakdown`, `overall_score`, `rank`, and
  `reasoning` (a few sentences generated only from the candidate's own scores — never fabricated).
- **ResearchResult**: one full run's output — `generated_at`, `ranking_formula_version`, the ranked
  candidate list, and any `source_errors`.

All four types support `to_dict()`/`from_dict()` for JSON persistence and round-trip cleanly.

## Content pillars and roles

`content_pillar` (see CLAUDE.md "Business signals"): `buying_advice`, `hardware_comparison`,
`optimization`, `gaming_technology`, `mistakes_and_myths`, `game_recommendations`,
`monthly_games`.

`content_role`: `growth`, `revenue`, or `hybrid` — the primary purpose of the content.

`monetization_path` is a short free-text description (e.g. "GPU affiliate links", "audience growth
only") and is never forced onto a candidate with no obvious commercial angle.

## The "What to play this month" pillar

A `monthly_games` candidate represents **one game recommendation for one hardware tier** —
`hardware_tier` (`low_end` / `mid_range` / `high_end`), `target_gpu_class` (free text),
`game_price_type` (`free` / `paid` / `unknown`), `release_relevance` (`new_release` / `recent` /
`evergreen` / `unknown`), and `game_title`. Grouping several such candidates into one monthly video
is a Script Agent concern (not implemented yet).

`scripts/research/game_history.py` appends one entry per `monthly_games` candidate with a
`game_title` to `data/research/game_history.json` on every run. **V0.1 only records this history —
it does not yet use it to avoid repeats.** That selection logic is a V0.2+ concern; the persisted
format (plain JSON list of `{candidate_id, game_title, hardware_tier, game_price_type,
content_pillar, recommended_at}`) is designed so it can be queried later without changing this
module's public functions.

No benchmark scraping and no fabricated FPS/performance numbers exist anywhere in this stage (see
CLAUDE.md "Game recommendation integrity").

## Heuristic vs real data

**Everything scored in V0.1 is heuristic.** There is no analytics, search-volume, or competition
data source yet. `scoring.py` computes each dimension from the candidate's own fields (pillar,
role, monetization path, title keywords, source freshness) — documented, deterministic rules, not
guesses dressed up as data. Every dimension a candidate receives this way is listed in
`ScoreBreakdown.heuristic_dimensions`, and the summary Markdown labels each one `(heuristic)`.

The one hook for future real data: if a candidate's `raw_metadata["known_scores"]` dict contains a
value for a dimension (e.g. `{"audience_interest": 8.5}` from a future real analytics source), that
value is used as-is and the dimension is removed from `heuristic_dimensions`. This lets a future
version replace individual heuristic scores with real evidence without changing the data model.

Never fabricated: search volume, exact demand/competition numbers, FPS benchmarks, sales numbers,
or popularity statistics (see CLAUDE.md "Research and opportunity scoring rules" and "Fact
checking"). Where no reliable number exists, `scoring.py` uses a stated neutral default (e.g.
`competition` defaults to 5.0, the scale midpoint) rather than inventing one.

## Ranking formula (v0.1)

`ranking.py` computes `overall_score` as a weighted sum over `RANKING_WEIGHTS` (a plain dict, easy
to retune). Two dimensions — `production_difficulty` and `competition` — are "inverted" (a higher
raw score is worse), so their contribution uses `(10 - value)`.

| Dimension | Weight | Direction |
|---|---|---|
| audience_interest | 0.16 | normal |
| retention_potential | 0.14 | normal |
| affiliate_potential | 0.14 | normal |
| commercial_intent | 0.12 | normal |
| confidence | 0.10 | normal |
| freshness | 0.08 | normal |
| evergreen_value | 0.08 | normal |
| novelty | 0.06 | normal |
| production_difficulty | 0.05 | inverted |
| visual_potential | 0.04 | normal |
| competition | 0.03 | inverted |

Weights sum to 1.0 (enforced at import time). Rationale (see CLAUDE.md "Ranking" and
docs/BUSINESS_STRATEGY.md): the business objective is reaching the first $100 in affiliate revenue
while still growing an audience, so growth signals (`audience_interest`, `retention_potential`) and
monetization signals (`commercial_intent`, `affiliate_potential`) are weighted close to evenly
rather than maximizing commercial intent alone. `competition` gets a deliberately small weight
because there is no authoritative competition data source yet — it should nudge ranking, not
dominate it. `RANKING_FORMULA_VERSION` ("v0.1") is persisted with every result so later weight
changes are identifiable in historical data.

## Sources

`ResearchSource` (`sources/base.py`) is a one-method interface: `fetch() -> list[ResearchCandidate]`,
expected to raise on failure. This is the deliberate "provider abstraction" exception from
CLAUDE.md's coding standards — not a plugin framework, just a thin interface plus a small builder
registry in `cli.py::SOURCE_BUILDERS`.

- **FixtureResearchSource**: reads `scripts/research/config/fixture_candidates.json`. Deterministic,
  no network. Used for local testing and development.
- **RssResearchSource**: reads a public RSS feed via `urllib` + `xml.etree` (standard library only —
  no API key, no extra dependency). Since an RSS item is just a headline/link/summary, the feed's
  `content_pillar`/`content_role` defaults come from configuration, not inference.

`scripts/research/config/sources.json` lists which sources run and their configuration (feed URLs,
per-feed pillar/role defaults, fixture path) — kept out of Python business logic so sources can be
added, disabled, or retargeted without a code change. `cli.py::collect_candidates` fetches from
every configured source; a failing source is logged (`ResearchResult.source_errors`) and skipped —
it never aborts the run.

## Adding a new source

1. Implement `ResearchSource.fetch()` in a new module under `scripts/research/sources/`.
2. Add a builder function to `cli.py::SOURCE_BUILDERS` keyed by a new `"type"` string.
3. Add an entry to `scripts/research/config/sources.json`.

No other module needs to change.

## Output

Each run writes, under `data/research/YYYY-MM-DD/`:

- `research_results.json` — the full `ResearchResult`, reloadable via
  `persistence.load_research_results()`.
- `summary.md` — ranked candidates with pillar, role, overall score, full score breakdown (each
  dimension labeled heuristic or real data), monetization path, confidence, source, and
  score-grounded reasoning.

And, at `data/research/game_history.json` (not dated — accumulates across runs): recommended-game
history for the monthly pillar (see above).

## Possible V0.2 directions (not implemented)

- Use `game_history.json` to actively avoid repeating the same games instead of only recording them.
- Add a second real source (another RSS feed, or a free-tier API) to validate the source
  abstraction with more than one live provider.
- Fact-check hooks: a place for the future Fact Checking stage to attach verified claims to a
  candidate before Script Agent ever sees it.
- Replace individual heuristic dimensions with real data as free-tier analytics/trend sources are
  identified, via the existing `known_scores` hook — no data model change needed.

Deciding and implementing V0.2 is a separate, explicitly scoped task.
