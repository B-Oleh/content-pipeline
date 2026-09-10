# Research Agent

First functional pipeline stage (see CLAUDE.md "Pipeline stages", stage 1). Collects candidate
gaming-content topics from several no-cost sources, deduplicates and scores them, ranks them, and
persists the results. Does not select topics for production on its own — later stages (Fact
Checking, Script Agent, Telegram Approval; not yet implemented) still gate what actually gets made.

Engineering rules (secrets, dependencies, logging, testing, etc.) are defined once in CLAUDE.md and
are not repeated here.

Version history: **V0.1** shipped a deterministic fixture source plus one RSS source and pure
heuristic scoring. **V0.2** added real multi-source research, source health reporting,
deduplication, evidence tracking, explicit freshness tiers, and an active game-history repetition
penalty. **V0.3** (current) proves the agent runs end-to-end on a real GitHub Actions runner via a
manually triggered workflow, and moves persistent state out of the gitignored `data/` directory
into a Git-tracked `state/` directory so it survives across separate workflow runs — see the
sections below for each.

## Module map

```
scripts/config.py                 STATE_DIR (state/), DATA_DIR (data/), etc. -- see "Git-backed
                                   persistent state" below
scripts/utils/atomic_write.py     atomic_write_text() -- used by every persistent AND transient
                                   JSON/Markdown write in this stage
scripts/research_agent.py         CLI entry point (python -m scripts.research_agent)
scripts/research/
  models.py                        ResearchCandidate, ScoreBreakdown, ScoredCandidate,
                                    ResearchResult, SourceHealth, FreshnessTier
  scoring.py                       Heuristic scoring engine
  ranking.py                       Weighted ranking formula + game-history repetition penalty
  freshness.py                     Explicit, configurable freshness-tier classification
  dedup.py                         Conservative per-run duplicate detection (URL / title)
  source_health.py                 Error classification + degraded-run detection
  persistence.py                   JSON + Markdown output under data/research/YYYY-MM-DD/ (transient)
  game_history.py                  Persisted, actively-used history of recommended games
                                    (state/research/game_history.json -- persistent)
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
.github/workflows/research_agent.yml   Manually triggered (workflow_dispatch only) cloud run
state/research/game_history.json  Git-tracked persistent state (see "Git-backed persistent state")
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

Two different lifetimes exist in this stage's output, and they are kept in two different,
never-nested directory trees (enforced by tests -- see `tests/research/test_cli.py`):

- **TRANSIENT** — `data/research/YYYY-MM-DD/` (`research_results.json`, `summary.md`) and
  `logs/`: safe to discard after review; a new dated folder every run. Nothing else in the pipeline
  depends on last run's copy still being there. Gitignored, as it always was.
- **PERSISTENT** — `state/research/game_history.json`, written via
  `scripts/research/state/store.py::JsonListStore`: must survive across runs, or the "don't repeat
  the same game every month" behavior in "Game history repetition" below silently stops working.
  Since V0.3, this lives under the Git-tracked `state/` directory (`scripts/config.py::STATE_DIR`),
  not under `data/` -- see "Git-backed persistent state" below for why.

Both trees are derived from `scripts/config.py::BASE_DIR`, never a hard-coded absolute path, so the
same code resolves correctly regardless of where the repository happens to be checked out (a
developer's machine, or a GitHub Actions runner's `$GITHUB_WORKSPACE`) -- local and cloud execution
share the exact same CLI and the exact same persistence abstraction, with no environment-specific
branching in Python. The state directory is also independently configurable per invocation via
`--state-dir` (and the output directory via `--output-dir`), so a caller never needs to hard-code
either path.

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
between two writers is still a lost update, not corruption) -- see "Concurrency" below for how the
GitHub Actions workflow avoids that scenario without building any locking of its own.

## Git-backed persistent state

GitHub Actions runners are ephemeral: every workflow run starts from a fresh checkout, so anything
written only to the runner's local disk is gone once the job ends. `state/` solves this by being an
ordinary, Git-tracked part of the repository: the GitHub Actions workflow (see "GitHub Actions
workflow" below) commits and pushes `state/` changes back to the repository at the end of a
successful run, so the next run's checkout starts from the previous run's updated state.

**This is an intentional, simple MVP choice, not the final answer.** Git-backed state was chosen
because it costs nothing extra (no new service, no new credential, fits the $0 Stage 1 budget in
docs/BUSINESS_STRATEGY.md), needs no new dependency, and is trivially inspectable (`git log --
state/` is a complete audit trail of every research run's effect on persistent state, for free).
It is not designed to scale: every state-changing run adds a commit, and this is not meant to
survive high write frequency, multiple concurrent writers, or non-JSON/large state.

**This can be replaced later without changing Research Agent's business logic.** `game_history.py`
and `ranking.py` only ever call `JsonListStore`'s `load()`/`save()`/`append()`/`extend()`; nothing
in this stage's scoring, ranking, deduplication, or source-health logic depends on state being
stored in Git specifically. A future version can reimplement `JsonListStore` against SQLite, a
small managed database, or an external store (see the V0.2 doc history for other options that were
considered) by changing that one class -- and, separately, removing the workflow's commit/push
steps -- without touching any caller.

Explicitly out of scope for "source of truth" here (per this task's own constraint, kept as a
standing rule): GitHub Actions **cache** (`actions/cache`) and workflow **artifacts** are not
used to store `game_history.json`. Both are convenience/diagnostic mechanisms with no durability
guarantee (cache entries can be evicted; artifacts expire on a retention schedule -- see "Transient
artifact vs persistent state" below) and neither is a place business logic should ever read
authoritative state from.

## Transient artifact vs persistent state

Every workflow run also uploads `data/research/` (that run's `research_results.json` and
`summary.md`) as a GitHub Actions artifact, named `research-agent-output-<run number>`, retained
for 7 days. This exists purely for human inspection/debugging of a specific run (e.g. "what did the
agent actually see and rank on run #42") -- it is never read back by any part of the pipeline, and
it is not where persistent state lives (that is `state/research/`, committed to Git -- see above).
Losing an old artifact to its retention period has no effect on the pipeline's behavior.

## Concurrency

Two Research Agent runs updating `state/` at the same time could conflict or corrupt the git-backed
state (e.g. two workflow runs each trying to push a commit based on the same starting point). The
simplest reasonable protection, and all that V0.3 adds, is a GitHub Actions
[concurrency group](https://docs.github.com/actions/using-jobs/using-concurrency) on the workflow:

```yaml
concurrency:
  group: research-agent-state
  cancel-in-progress: false
```

This serializes `workflow_dispatch` runs of this specific workflow through GitHub's own queue --
a second manual run started while one is in progress waits for the first to finish instead of
running in parallel. This is not distributed locking and does not need to be: within GitHub
Actions, it is a built-in, zero-code guarantee. It does **not** protect against a human running
`python -m scripts.research_agent` locally and pushing `state/` changes at the same moment a
workflow run is doing the same -- that remains a plain Git push race, handled the same way any
conflicting push is (see the next paragraph), not by anything special to this stage.

The workflow's push step never force-pushes. If the remote `state/` has moved since checkout (e.g.
a conflicting local push happened in between), `git push` fails normally and the workflow reports
that failure clearly (a `::error::` annotation and a non-zero exit) instead of overwriting
conflicting remote state -- the fix is to re-run the workflow (or resolve manually), not to retry
with `--force`.

## GitHub Actions workflow

`.github/workflows/research_agent.yml` -- manually triggered only (`workflow_dispatch`, no
`schedule`/`cron`/`repository_dispatch`; see CLAUDE.md "Cloud execution and budget": scheduling is
deliberately not added until the MVP is stable). Purpose: prove the agent runs end-to-end on a
clean Linux runner, that real RSS network requests work there, and that persistent state survives
across separate runs -- not to automate production yet.

Steps, in order: checkout -> set up Python 3.11 -> install dependencies -> run the full automated
test suite (a failure here stops the job before touching `state/`) -> run Research Agent against
the real configured sources (`--output-dir data/research --state-dir state/research`, the same CLI
a local developer would run) -> print the summary into the workflow's own job summary and flag a
degraded or zero-candidate run with `::warning::`/`::error::` annotations -> upload `data/research/`
as a short-retention artifact -> detect whether `state/` changed -> if so, verify the change is
JSON-only, commit it with the recognizable message `chore(state): update research state`, and push.
No commit is created when `state/` did not change.

Commit safety: the workflow only ever runs `git add state/` (never `git add -A`/`git add .`), so
the automated commit cannot contain source code, workflow files, secrets, `.env`, logs, transient
`data/`, or any other working-tree change -- by construction, not by convention. It additionally
refuses to commit if anything under `state/` is not a `.json` file, as defense in depth.

Permissions: `permissions: contents: write` at the workflow level -- the minimum needed to push the
state commit, and nothing else (no issues/PR/package scopes). The GitHub Actions runner's own
network stack is used unmodified for RSS requests: no TLS bypass, no disabled certificate
verification (see "Sources" above and CLAUDE.md's security posture) -- the runner is the real
network-compatibility test this stage needed, which the previous development environment's
intercepting TLS proxy could not provide.

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

TRANSIENT, under `data/research/YYYY-MM-DD/` (gitignored; see "Persistent state vs transient
output" above):

- `research_results.json` — the full `ResearchResult`, reloadable via
  `persistence.load_research_results()`.
- `summary.md` — ranked candidates with pillar, role, overall score, full score breakdown (each
  dimension labeled heuristic or real data), monetization path, confidence, freshness tier,
  repetition penalty (if any), evidence sources, and score-grounded reasoning; plus a source-health
  section and a degraded-run warning banner when applicable.

PERSISTENT, under `state/research/` (Git-tracked; not dated — accumulates across runs; see
"Git-backed persistent state" above): `game_history.json`.

Also transient: `logs/` (runtime log files) — never uploaded as a workflow artifact, never
committed, and not expected to survive past the run that produced them.

## Possible V0.4 directions (not implemented)

- Extend the same persistent-state approach to "previously selected topics" generally (not just
  games), if repeat non-game topics turn out to be a real problem in practice.
- Fact-check hooks: a place for the future Fact Checking stage to attach verified claims to a
  candidate before Script Agent ever sees it.
- Replace individual heuristic dimensions with real data as free-tier analytics/trend sources are
  identified, via the existing `known_scores` hook — no data model change needed.
- Consider letting `dedup.py` corroboration counts feed into `novelty`/`audience_interest` as well
  as `confidence`, if that turns out to correlate with anything once real performance data exists.
- Scheduling (cron) for the GitHub Actions workflow, once the manually triggered run has been
  exercised enough to trust it unattended — deliberately not part of V0.3 (see CLAUDE.md "Cloud
  execution and budget").
- Revisit Git-backed state (see "Git-backed persistent state" above) if commit volume, state size,
  or the need for concurrent writers ever makes it impractical.

Deciding and implementing V0.4 is a separate, explicitly scoped task.
