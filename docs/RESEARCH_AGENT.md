# Research Agent

First functional pipeline stage (see CLAUDE.md "Pipeline stages", stage 1). Collects candidate
gaming-content topics from several no-cost sources, deduplicates and scores them, ranks them, and
persists the results. Does not select topics for production on its own — later stages (Fact
Checking, Script Agent, Telegram Approval; not yet implemented) still gate what actually gets made.

Engineering rules (secrets, dependencies, logging, testing, etc.) are defined once in CLAUDE.md and
are not repeated here.

Version history: **V0.1** shipped a deterministic fixture source plus one RSS source and pure
heuristic scoring. **V0.2** (current) adds real multi-source research, source health reporting,
deduplication, evidence tracking, explicit freshness tiers, and an active game-history repetition
penalty — see the sections below for each.

## Module map

```
scripts/research_agent.py         CLI entry point (python -m scripts.research_agent)
scripts/research/
  models.py                        ResearchCandidate, ScoreBreakdown, ScoredCandidate,
                                    ResearchResult, SourceHealth, FreshnessTier
  scoring.py                       Heuristic scoring engine
  ranking.py                       Weighted ranking formula + game-history repetition penalty
  freshness.py                     Explicit, configurable freshness-tier classification
  dedup.py                         Conservative per-run duplicate detection (URL / title)
  source_health.py                 Error classification + degraded-run detection
  persistence.py                   JSON + Markdown output under data/research/YYYY-MM-DD/
  game_history.py                  Persisted, actively-used history of recommended games
  cli.py                           Orchestration: build sources -> fetch -> dedup -> score ->
                                    rank -> save -> record
  state/
    store.py                       JsonListStore -- generic persistent-state primitive
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
  optional fields for the monthly game pillar (`hardware_tier`, `target_gpu_class`,
  `game_price_type`, `release_relevance`, `game_title`), and `raw_metadata` — a free-form bucket
  used for `published_at`, `retrieved_at`, the `known_scores` override hook, and (since V0.2) the
  `evidence` list (see "Evidence" below).
- **ScoreBreakdown**: one 0-10 float per scoring dimension (see below), plus
  `heuristic_dimensions` — the set of dimensions whose value is a heuristic guess rather than real
  evidence.
- **ScoredCandidate**: a candidate plus its `ScoreBreakdown`, `overall_score`, `rank`,
  `reasoning` (a few sentences generated only from the candidate's own scores — never fabricated),
  and, since V0.2, `repetition_penalty` and `freshness_tier`.
- **SourceHealth** (V0.2): one source's outcome for one run — success/failure, item count,
  duration, retrieval timestamp, and an error category when it failed.
- **ResearchResult**: one full run's output — `generated_at`, `ranking_formula_version`, the ranked
  candidate list, `source_errors`, `source_health`, and the raw/deduplicated candidate counts.

All types support `to_dict()`/`from_dict()` for JSON persistence and round-trip cleanly; the V0.2
fields all have safe defaults so a V0.1-era persisted JSON file still loads.

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
`game_title` to persistent state (see "Persistent state vs transient output" below) on every run,
in a plain JSON list of `{candidate_id, game_title, hardware_tier, game_price_type,
content_pillar, recommended_at}` entries. Since V0.2, this history is also actively used by
`ranking.py` -- see "Game history repetition" below.

No benchmark scraping and no fabricated FPS/performance numbers exist anywhere in this stage (see
CLAUDE.md "Game recommendation integrity").

## Persistent state vs transient output

Two different lifetimes exist in this stage's output, and they are kept physically and
conceptually separate:

- **Transient per-run output** (`data/research/YYYY-MM-DD/research_results.json`, `summary.md`,
  and log files under `logs/`): safe to discard after review; a new dated folder every run. Nothing
  else in the pipeline depends on last week's copy still being there.
- **Persistent pipeline state** (currently `data/research/state/game_history.json`, written via
  `scripts/research/state/store.py::JsonListStore`): must survive across runs, or the "don't repeat
  the same game every month" behavior in "Game history repetition" below silently stops working.

`JsonListStore` is deliberately minimal (load/save/append/extend over one JSON file of plain
dicts) -- not a database. It exists so callers (currently only `game_history.py`) never read/write
JSON directly, which keeps the backing store replaceable: a future version could reimplement this
one class on SQLite or a small managed database without changing any caller.

Its writes go through `scripts/utils/atomic_write.py::atomic_write_text()` (write to a temp file in
the same directory, `fsync`, then `os.replace()`), not a direct `Path.write_text()`. A direct write
that gets interrupted (process killed, crash, power loss) can leave a truncated, unparsable file
behind -- for `game_history.json` specifically, that would mean every subsequent run's
`load_game_history()` raises `json.JSONDecodeError` and the pipeline never recovers on its own.
`atomic_write_text()` guarantees a reader always sees either the complete previous content or the
complete new content, never a partial write; `persistence.py`'s transient output goes through the
same helper for the same reason. This does **not** make concurrent writers safe together (a race
between two writers is still a lost update, not corruption) -- nothing in this stage currently runs
concurrently, so that is a documented limitation, not a bug fixed here.

**This does not yet survive GitHub Actions.** GitHub Actions runners are ephemeral: every workflow
run starts from a fresh checkout, so anything written to `data/` (currently entirely gitignored,
per CLAUDE.md's "Repository structure") is gone by the next run. That means `game_history.json`
would silently reset to empty on every scheduled run once GitHub Actions is introduced -- exactly
the failure mode that made the repetition penalty worth building. Do not implement GitHub Actions
around this without first closing that gap. Options for whoever implements the GitHub Actions
stage (V0.3+), in rough order of $0-budget simplicity, to decide on then:

1. **Commit the state file back to the repository** after each run (a bot commit touching only
   `data/research/state/game_history.json`, which would need an explicit `.gitignore` exception for
   that one path). Simplest, fully $0, but means the workflow needs write access back to the repo.
2. **`actions/cache`** keyed on a stable key. Free and simple, but cache entries are not a durability
   guarantee -- GitHub can and does evict them (size/age limits), so this is a "probably fine most of
   the time" option, not a real persistence guarantee.
3. **External store** (a private Gist, a small object-storage bucket, or a lightweight managed
   database) if/when the state needs to be shared across more than one workflow or written from
   somewhere other than the scheduled run.

Whichever option is chosen still goes through the "API integrations" and "Human approval required"
rules in CLAUDE.md if it introduces any paid service or new credential.

## Game history repetition

A repeated game recommendation is not automatically banned (see CLAUDE.md "Game recommendation
integrity") -- `ranking.py::compute_repetition_penalty()` turns "how recently was this exact game
recommended" into a configurable score penalty applied after the weighted ranking sum:

- Recommended recently **for the same hardware tier**: a strong penalty (`REPETITION_PENALTY_RULES`
  in `ranking.py`, e.g. within 14 days -> -4.0).
- Recommended a long time ago (beyond the last configured threshold, currently 180 days): no
  penalty.
- Recommended before **for a different hardware tier**: only the same table's penalty, scaled down
  by `CROSS_TIER_PENALTY_MULTIPLIER` (0.25) -- a legitimately distinct angle is barely discouraged.

The applied penalty and a human-readable, history-grounded note (e.g. `'Hades II' was previously
recommended for this hardware tier 10 day(s) ago`) are both attached to the `ScoredCandidate` and
shown in `summary.md` -- never a fabricated rationale, only what the persisted history actually
says. `rank_candidates()` accepts `game_history` and `as_of` as optional parameters; omitting them
(the V0.1 call shape) is equivalent to an empty history, so no candidate is ever penalized without
it being explicitly requested.

## Deduplication

Real sources frequently report the same story. `dedup.py::deduplicate_candidates()` runs once per
run, right after collection and before scoring, and merges a candidate into an existing one only
when either:

- their `source_url`s are identical after normalization (scheme/host lowercased, trailing slash and
  query string/fragment stripped -- tracking parameters commonly differ across shares of the same
  story), or
- their titles are identical after normalization (lowercased, punctuation stripped, whitespace
  collapsed).

This is deliberately conservative: two headlines that merely share common gaming words (e.g. "GPU",
"RTX", "best") are never merged, only an exact match after normalization. A merged-away duplicate is
never silently dropped -- it contributes one entry to the kept candidate's evidence list (see
"Evidence" below), so multiple sources reporting the same story becomes visible corroboration
instead of vanishing.

## Evidence

Every candidate's `raw_metadata["evidence"]` is a list of `{source_name, source_url, title,
summary, published_at, retrieved_at}` entries -- one per source item that contributed to it (one
entry normally; more than one after `dedup.py` merges corroborating reports). This is the
provenance a future Fact Checker needs to answer "why does the system believe this topic is
currently relevant": every claim traces back to a named source, a URL, and a retrieval time, never
to an unlabeled heuristic guess. `summary.md` lists each candidate's evidence sources; `scoring.py`
gives a modest, capped confidence bump to a candidate corroborated by 2+ independent sources (see
"Heuristic vs real data" below) -- that corroboration is itself real evidence, not a guess.

## Source health

`cli.py::collect_candidates()` records one `SourceHealth` entry per configured source, every run:
success/failure, item count, duration, retrieval timestamp, and (on failure) an error category from
`source_health.py::classify_error()` (`timeout`, `network`, `tls_error`, `parse_error`,
`config_error`, or `unknown`). `source_health.py::is_degraded()` flags a run where at least half of
the configured sources failed (`DEGRADED_FAILURE_RATIO`, configurable). `summary.md` always shows
per-source health, and prints a prominent warning banner when a run is degraded -- a failed source
is logged AND visible in the output that a human reviews, never just hidden in a log file.

## Freshness

`freshness.py` classifies a source item's age into one of three explicit tiers (`FreshnessTier` in
models.py), via a small, configurable threshold table (`FRESHNESS_RULES`): `very_recent` (<= 2
days), `recent` (<= 14 days), `older_evergreen` (older, or no verified publish date -> `unknown`
maps to the same neutral score as `older_evergreen`). This tier is surfaced explicitly on every
`ScoredCandidate` (`freshness_tier`) in addition to feeding the numeric `freshness` scoring
dimension (`scoring.py` delegates to this module so the two never drift apart). Freshness is
evidence only that a source item is recent -- it is never treated as, or substituted for, a
popularity/demand signal (see CLAUDE.md "Research and opportunity scoring rules").

## Heuristic vs real data

**Almost everything scored is still heuristic.** There is no analytics or search-volume
integration yet. `scoring.py` computes each dimension from the candidate's own fields (pillar,
role, monetization path, title keywords, freshness tier) — documented, deterministic rules, not
guesses dressed up as data. Every dimension a candidate receives this way is listed in
`ScoreBreakdown.heuristic_dimensions`, and the summary Markdown labels each one `(heuristic)`.

The one hook for future real data: if a candidate's `raw_metadata["known_scores"]` dict contains a
value for a dimension (e.g. `{"audience_interest": 8.5}` from a future real analytics source), that
value is used as-is and the dimension is removed from `heuristic_dimensions`. This lets a future
version replace individual heuristic scores with real evidence without changing the data model.
Malformed overrides (not a dict, or a non-numeric value) are logged and ignored rather than
crashing the run -- a malformed candidate should never take down the rest of a batch.

The one exception, since V0.2: `confidence` gets a modest, capped boost when a candidate is
corroborated by 2+ independent sources (see "Evidence" above) -- multiple sources independently
reporting the same story is genuine evidence the topic is currently relevant, not a guess. It is
still capped at 10.0 and still clearly heuristic (a count of corroborating sources, not a
statistical confidence interval).

Never fabricated: search volume, exact demand/competition numbers, FPS benchmarks, sales numbers,
or popularity statistics (see CLAUDE.md "Research and opportunity scoring rules" and "Fact
checking"). Where no reliable number exists, `scoring.py` uses a stated neutral default (e.g.
`competition` defaults to 5.0, the scale midpoint) rather than inventing one.

## Ranking formula

`ranking.py` computes `overall_score` as a weighted sum over `RANKING_WEIGHTS` (a plain dict, easy
to retune). Two dimensions — `production_difficulty` and `competition` — are "inverted" (a higher
raw score is worse), so their contribution uses `(10 - value)`. Since V0.2, the game-history
repetition penalty (see "Game history repetition" above) is subtracted from this weighted sum
afterward, floored at 0 -- it is a ranking-time adjustment, not one of the 11 documented scoring
dimensions, so it never changes `ScoreBreakdown`'s scale.

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
dominate it. `RANKING_FORMULA_VERSION` (currently `"v0.2"`, bumped from `"v0.1"` when the
repetition penalty was added) is persisted with every result so later formula changes are
identifiable in historical data.

## Sources

`ResearchSource` (`sources/base.py`) is a one-method interface: `fetch() -> list[ResearchCandidate]`,
expected to raise on failure. This is the deliberate "provider abstraction" exception from
CLAUDE.md's coding standards — not a plugin framework, just a thin interface plus a small builder
registry in `cli.py::SOURCE_BUILDERS`.

- **FixtureResearchSource**: reads `scripts/research/config/fixture_candidates.json`. Deterministic,
  no network. Used for local testing and development.
- **RssResearchSource**: reads a public RSS feed via `urllib` + `xml.etree` (standard library only —
  no API key, no extra dependency). Sends a descriptive (non-spoofed) `User-Agent` identifying this
  project, since some feed hosts otherwise reject or rate-limit the default urllib identifier. Since
  an RSS item is just a headline/link/summary, the feed's `content_pillar`/`content_role` defaults
  come from configuration, not inference. Currently configured against three real, publicly linked
  publisher RSS feeds (PC Gamer, Rock Paper Shotgun, Eurogamer) — legitimate syndication feeds, not
  scraping, requiring no credentials.

`scripts/research/config/sources.json` lists which sources run and their configuration (feed URLs,
per-feed pillar/role defaults, fixture path) — kept out of Python business logic so sources can be
added, disabled, or retargeted without a code change. `cli.py::collect_candidates` fetches from
every configured source with its own timeout (`RssResearchSource(timeout=...)`, configurable per
feed, 10s by default); a failing source is logged and recorded as a failed `SourceHealth` entry
(see "Source health" above) and skipped — it never aborts the run.

## Adding a new source

1. Implement `ResearchSource.fetch()` in a new module under `scripts/research/sources/`.
2. Add a builder function to `cli.py::SOURCE_BUILDERS` keyed by a new `"type"` string.
3. Add an entry to `scripts/research/config/sources.json`.

No other module needs to change.

## Output

Transient, under `data/research/YYYY-MM-DD/` (see "Persistent state vs transient output" above):

- `research_results.json` — the full `ResearchResult`, reloadable via
  `persistence.load_research_results()`.
- `summary.md` — ranked candidates with pillar, role, overall score, full score breakdown (each
  dimension labeled heuristic or real data), monetization path, confidence, freshness tier,
  repetition penalty (if any), evidence sources, and score-grounded reasoning; plus a source-health
  section and a degraded-run warning banner when applicable.

Persistent, under `data/research/state/` (not dated — accumulates across runs, and is the part
that needs a durability story before GitHub Actions -- see above): `game_history.json`.

## Possible V0.3 directions (not implemented)

- Close the GitHub Actions persistence gap identified above before introducing scheduled runs (see
  "Persistent state vs transient output").
- Extend the same persistent-state approach to "previously selected topics" generally (not just
  games), if repeat non-game topics turn out to be a real problem in practice.
- Fact-check hooks: a place for the future Fact Checking stage to attach verified claims to a
  candidate before Script Agent ever sees it.
- Replace individual heuristic dimensions with real data as free-tier analytics/trend sources are
  identified, via the existing `known_scores` hook — no data model change needed.
- Consider letting `dedup.py` corroboration counts feed into `novelty`/`audience_interest` as well
  as `confidence`, if that turns out to correlate with anything once real performance data exists.

Deciding and implementing V0.3 is a separate, explicitly scoped task.
