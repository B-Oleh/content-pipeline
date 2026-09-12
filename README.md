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

The repository is currently an early scaffold. **Research Agent** is implemented (see "Running
Research Agent" below and [docs/RESEARCH_AGENT.md](docs/RESEARCH_AGENT.md)), and a first working
**video production pipeline** (Script Agent through Telegram delivery) now turns one Research
Agent topic into a real rendered MP4 delivered to Telegram — see "Running the video production
pipeline" below and [docs/PRODUCTION_PIPELINE.md](docs/PRODUCTION_PIPELINE.md) for its design. No
YouTube publishing, scheduling, analytics, or Telegram approval buttons exist yet.

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
  produce_video.py     CLI entry point for the video production pipeline
  production/           Script Agent -> Assets -> Voice -> Subtitles -> Render -> QA -> Telegram
    providers/            Gemini / Pexels / Pixabay / edge-tts / Telegram provider boundaries
  utils/              Shared helper modules (e.g. logging, atomic file writes)
tests/              Automated tests (pytest)
  research/           Tests for Research Agent
  production/          Tests for the video production pipeline
data/               Transient per-run pipeline output (topics, scripts, metadata) — git-ignored
state/              Persistent pipeline state (e.g. game recommendation history) — tracked in Git,
                    NOT git-ignored (see docs/RESEARCH_AGENT.md "Git-backed persistent state")
assets/             Downloaded/generated production assets (footage, images, audio) — git-ignored
output/             Finished rendered videos (e.g. final_video.mp4) — git-ignored, never committed
logs/               Runtime log files — git-ignored
docs/               Supporting documentation (e.g. BUSINESS_STRATEGY.md, RESEARCH_AGENT.md,
                    PRODUCTION_PIPELINE.md)
.github/workflows/  GitHub Actions -- research_agent.yml and produce_video.yml (both manual,
                    workflow_dispatch only)
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

   `.env` is git-ignored — it will hold local secrets. Research Agent needs no API keys. The video
   production pipeline (see "Running the video production pipeline" below) needs `GEMINI_API_KEY`,
   `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` if you run it
   locally; in GitHub Actions these come from GitHub Secrets instead (see that section).

5. FFmpeg is required for the video production pipeline (not for Research Agent). Install it via
   your OS package manager (e.g. `winget install Gyan.FFmpeg` on Windows, `apt-get install ffmpeg`
   on Ubuntu, `brew install ffmpeg` on macOS) and confirm `ffmpeg -version` and `ffprobe -version`
   both work. GitHub Actions installs it automatically if missing (see
   docs/PRODUCTION_PIPELINE.md).

6. Run the test suite to verify the setup:

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

## Running the video production pipeline

Turns one Research Agent topic into a real rendered vertical MP4 delivered to Telegram: Script
Agent (Gemini) -> Asset Acquisition (Pexels/Pixabay) -> Voice (edge-tts) -> Subtitles -> Video
Assembly (ffmpeg) -> Automated QA -> Telegram delivery. See
[docs/PRODUCTION_PIPELINE.md](docs/PRODUCTION_PIPELINE.md) for the full design. Requires the five
secrets listed above (as environment variables locally, or GitHub Secrets in CI) and a local
ffmpeg/ffprobe install.

```bash
python -m scripts.produce_video
```

This runs preflight first (fails fast and clearly if any of the 5 secrets is missing or a service
is unreachable — never prints secret values), then the full pipeline, writing intermediate files to
`data/production/` (transient) and the final video to `output/final_video.mp4` (also transient,
**never committed to Git**). If Automated QA fails, the video is **not** sent to Telegram.
Topics outside explicit PC/gaming subject matter are rejected. Before rendering, at least 80% of
the scene timeline must use accepted Pexels/Pixabay media, with no consecutive pure info cards.
If a topic fails, production tries the next eligible candidate; if none passes, it stops.
`data/production/visual_attempts.json` records results and `attempts/` holds per-topic artifacts.


## Running the video production pipeline on GitHub Actions

`.github/workflows/produce_video.yml` — **manually triggered only** (`workflow_dispatch`; no
schedule/cron). This is the real end-to-end test: a clean Ubuntu runner, real network access to
Gemini/Pexels/Pixabay/edge-tts/Telegram, and ffmpeg installed automatically if not already present.

**To run it, in the GitHub web UI:**

1. Open the repository's **Actions** tab.
2. Select **"Produce Video (manual)"** in the left-hand workflow list.
3. Click **"Run workflow"**, choose the branch (normally `main`), and confirm.
4. Watch the run: test suite → preflight (fails immediately and clearly if a secret is missing or a
   service is unreachable) → the full pipeline. On success, the video has already been sent to your
   Telegram chat by the time the run finishes.
5. If anything fails, a `produce-video-diagnostics-<run number>` artifact (3-day retention) is
   uploaded automatically with the intermediate script/asset/QA files for debugging — this never
   includes secrets and is never published anywhere.

**Prerequisite:** all 5 secrets — `GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — must already be configured under **Settings → Secrets
and variables → Actions**. This workflow only reads the repository (`permissions: contents: read`)
— unlike `research_agent.yml`, it never commits or pushes anything, so no "Read and write
permissions" setting is needed for it specifically.

## Notes

- Secrets are never hard-coded — local secrets go in `.env` (git-ignored); in CI they come from
  GitHub Secrets. Research Agent needs none. The video production pipeline needs
  `GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` —
  never printed or logged anywhere (see docs/PRODUCTION_PIPELINE.md "Secrets").
- `assets/`, `output/`, `data/`, and `logs/` are git-ignored except for a `.gitkeep` placeholder,
  so generated media, transient pipeline output, rendered videos, and log files never get committed.
- `state/` is the one exception: it is intentionally tracked in Git (see "Running Research Agent"
  above and docs/RESEARCH_AGENT.md "Git-backed persistent state"). The video production pipeline
  never writes to it (see docs/PRODUCTION_PIPELINE.md "Relationship to Research Agent state").
- See [CLAUDE.md](CLAUDE.md) for the full pipeline design, coding standards, and the rules that
  govern adding dependencies or API integrations.
- See [docs/BUSINESS_STRATEGY.md](docs/BUSINESS_STRATEGY.md) for the business model, revenue
  milestones, and content niche.

Overnight batch: `python -m scripts.produce_batch` delivers up to three candidates for morning review; see [Batch mode](docs/BATCH_MODE.md).
