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
Agent** (V0.3), is implemented — see "Running Research Agent" and "Running Research Agent on
GitHub Actions" below, and [docs/RESEARCH_AGENT.md](docs/RESEARCH_AGENT.md) for its design. No
other pipeline stage or external API integration has been implemented yet.

## Repository structure

This is the canonical, detailed listing — CLAUDE.md references this section instead of
duplicating it.

```
scripts/            Python automation and pipeline code
  config.py           Shared configuration and directory paths (incl. STATE_DIR)
  research_agent.py   CLI entry point for Research Agent
  research/            Research Agent package (models, scoring, ranking, sources, persistence)
    sources/             Research source implementations (fixture, RSS)
    state/               Persistent pipeline state primitive (JsonListStore)
    config/              Source configuration and fixture data (JSON)
  utils/              Shared helper modules (e.g. logging, atomic file writes)
tests/              Automated tests (pytest)
  research/           Tests for Research Agent
data/               Transient per-run pipeline output (topics, scripts, metadata) — git-ignored
state/              Persistent pipeline state (e.g. game recommendation history) — tracked in Git,
                    NOT git-ignored (see docs/RESEARCH_AGENT.md "Git-backed persistent state")
assets/             Downloaded/generated production assets (footage, images, audio) — git-ignored
output/             Finished rendered videos — git-ignored
logs/               Runtime log files — git-ignored
docs/               Supporting documentation (e.g. BUSINESS_STRATEGY.md, RESEARCH_AGENT.md)
.github/workflows/  GitHub Actions -- research_agent.yml (manual, workflow_dispatch only)
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
`data/research/YYYY-MM-DD/` (transient, gitignored), and updates the persistent
game-recommendation history at `state/research/game_history.json` (tracked in Git; used to
penalize recommending the same game again too soon — see docs/RESEARCH_AGENT.md "Game history
repetition").

Options:

```bash
python -m scripts.research_agent --config path/to/sources.json --output-dir path/to/output --state-dir path/to/state
```

Source configuration (which feeds/fixtures run, and their defaults) lives in
`scripts/research/config/sources.json` — no source URLs are hard-coded in the Python logic. If a
configured source fails (e.g. a feed is unreachable), the run continues with the remaining
sources and reports the failure in `summary.md` instead of aborting or hiding it.

**TRANSIENT vs PERSISTENT, at a glance:**

| Path | Lifetime | Git status |
|---|---|---|
| `data/research/YYYY-MM-DD/` | Transient — one run, safe to delete | git-ignored |
| `logs/` | Transient — one run, safe to delete | git-ignored |
| `state/research/` | Persistent — must survive across runs | tracked in Git |

See docs/RESEARCH_AGENT.md "Git-backed persistent state" for why `state/` is committed to Git for
this $0 MVP stage, and why that is an intentional, explicitly replaceable choice rather than a
permanent architectural commitment.

## Running Research Agent on GitHub Actions

`.github/workflows/research_agent.yml` runs the exact same CLI on a GitHub-hosted Linux runner —
this is how the agent is validated against a real network (no TLS-intercepting proxy, unlike some
local/sandboxed dev environments) and proves persistent state survives across separate runs. It is
**manually triggered only** — no schedule/cron exists yet (see CLAUDE.md "Cloud execution and
budget").

**To run it, in the GitHub web UI:**

1. Open the repository's **Actions** tab.
2. Select **"Research Agent (manual)"** in the left-hand workflow list.
3. Click **"Run workflow"**, choose the branch (normally `main`), and confirm.
4. Once it finishes, open the run to see: the test suite result, the Research Agent summary
   printed into the run's own **Summary** page (source health, degraded-run warnings, ranked
   candidates), and a `research-agent-output-<run number>` artifact (7-day retention) containing
   that run's `research_results.json` and `summary.md` for closer inspection.
5. If `state/` changed (e.g. a new game recommendation was recorded), the workflow commits and
   pushes that change back to the branch with the commit message `chore(state): update research
   state` — check the branch's commit history afterward to confirm.

**One-time repository setting to check first:** this workflow needs permission to push its state
commit back to the repository. Go to **Settings → Actions → General → Workflow permissions** and
confirm **"Read and write permissions"** is selected (the workflow itself only ever requests
`contents: write`, the minimum needed — but the repository-level setting must allow at least that
much, or the push step will fail with a permissions error).

## Notes

- Secrets are never hard-coded — local secrets go in `.env` (git-ignored); this workflow needs no
  secrets at all today (no API keys, no credentials), so none are configured.
- `assets/`, `output/`, `data/`, and `logs/` are git-ignored except for a `.gitkeep` placeholder,
  so generated media, transient pipeline output, and log files never get committed.
- `state/` is the one exception: it is intentionally tracked in Git (see "Running Research Agent"
  above and docs/RESEARCH_AGENT.md "Git-backed persistent state").
- See [CLAUDE.md](CLAUDE.md) for the full pipeline design, coding standards, and the rules that
  govern adding dependencies or API integrations.
- See [docs/BUSINESS_STRATEGY.md](docs/BUSINESS_STRATEGY.md) for the business model, revenue
  milestones, and content niche.
