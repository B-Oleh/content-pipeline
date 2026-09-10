# content-pipeline

Automated short-form video content production pipeline for a **$0-cost autonomous gaming content
business** (primary target: YouTube Shorts). Two recurring content pillars: gaming hardware/setup
optimization/buying advice, and a monthly "What to play this month" game-recommendation series by
PC performance tier. See [docs/BUSINESS_STRATEGY.md](docs/BUSINESS_STRATEGY.md) for the business
model, revenue milestones, and both content pillars.

The system is being built incrementally, agent by agent, from research and scripting through
assembly, QA, human (Telegram) approval, and publishing. Full project mission, architecture, the
complete pipeline stage list, and engineering rules live in [CLAUDE.md](CLAUDE.md) — that file is
the source of truth for architecture and process.

The repository is currently an early scaffold. The first functional pipeline stage, **Research
Agent** (V0.2), is implemented — see "Running Research Agent" below and
[docs/RESEARCH_AGENT.md](docs/RESEARCH_AGENT.md) for its design. No other pipeline stage or
external API integration has been implemented yet.

## Repository structure

This is the canonical, detailed listing — CLAUDE.md references this section instead of
duplicating it.

```
scripts/            Python automation and pipeline code
  config.py           Shared configuration and directory paths
  research_agent.py   CLI entry point for Research Agent
  research/            Research Agent package (models, scoring, ranking, sources, persistence)
    sources/             Research source implementations (fixture, RSS)
    state/               Persistent pipeline state primitive (JsonListStore)
    config/              Source configuration and fixture data (JSON)
  utils/              Shared helper modules (e.g. logging, atomic file writes)
tests/              Automated tests (pytest)
  research/           Tests for Research Agent
data/               Structured pipeline data (topics, scripts, metadata) — git-ignored
assets/             Downloaded/generated production assets (footage, images, audio) — git-ignored
output/             Finished rendered videos — git-ignored
logs/               Runtime log files — git-ignored
docs/               Supporting documentation (e.g. BUSINESS_STRATEGY.md, RESEARCH_AGENT.md)
.github/workflows/  GitHub Actions (not yet used)
CLAUDE.md           Persistent project instructions and architecture documentation
```

## Local setup

Prerequisites: Python 3.11+.

1. Clone the repository and open a terminal in its root.
2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Create your local environment file:

   ```bash
   cp .env.example .env
   ```

   `.env` is git-ignored — it will hold local secrets once pipeline stages start
   requiring them. No API keys are required yet.

5. Run the test suite to verify the setup:

   ```bash
   pytest
   ```

## Running Research Agent

Research Agent collects candidate gaming-content topics from several no-cost sources (a
deterministic fixture plus real publisher RSS feeds), deduplicates and scores them, ranks them,
and persists the results. It requires no API keys or paid services. See
[docs/RESEARCH_AGENT.md](docs/RESEARCH_AGENT.md) for the full design (data model, scoring
dimensions, ranking formula, deduplication, source health, freshness, source configuration).

```bash
python -m scripts.research_agent
```

This writes `research_results.json` (structured data) and `summary.md` (human-readable,
including per-source health and a degraded-run warning when too many sources failed) to
`data/research/YYYY-MM-DD/`, and updates the persistent game-recommendation history at
`data/research/state/game_history.json` (used to penalize recommending the same game again too
soon — see docs/RESEARCH_AGENT.md "Game history repetition").

Options:

```bash
python -m scripts.research_agent --config path/to/sources.json --output-dir path/to/output --state-dir path/to/state
```

Source configuration (which feeds/fixtures run, and their defaults) lives in
`scripts/research/config/sources.json` — no source URLs are hard-coded in the Python logic. If a
configured source fails (e.g. a feed is unreachable), the run continues with the remaining
sources and reports the failure in `summary.md` instead of aborting or hiding it.

**Note:** `data/research/state/` currently lives under the gitignored `data/` directory, so it
does not yet survive across separate GitHub Actions runs (which aren't implemented yet). See
docs/RESEARCH_AGENT.md "Persistent state vs transient output" for the tracked gap and the options
for closing it when GitHub Actions is introduced.

## Notes

- Secrets are never hard-coded — local secrets go in `.env` (git-ignored); CI secrets will use
  GitHub Secrets once GitHub Actions workflows are introduced.
- `assets/`, `output/`, `data/`, and `logs/` are git-ignored except for a `.gitkeep` placeholder,
  so generated media, pipeline data, and log files never get committed.
- See [CLAUDE.md](CLAUDE.md) for the full pipeline design, coding standards, and the rules that
  govern adding dependencies or API integrations.
- See [docs/BUSINESS_STRATEGY.md](docs/BUSINESS_STRATEGY.md) for the business model, revenue
  milestones, and content niche.
