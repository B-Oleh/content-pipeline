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

The repository is currently an early scaffold: this is the development foundation only, no
pipeline stage or external API integration has been implemented yet.

## Repository structure

This is the canonical, detailed listing — CLAUDE.md references this section instead of
duplicating it.

```
scripts/            Python automation and pipeline code
  config.py           Shared configuration and directory paths
  utils/              Shared helper modules (e.g. logging)
tests/              Automated tests (pytest)
data/               Structured pipeline data (topics, scripts, metadata) — git-ignored
assets/             Downloaded/generated production assets (footage, images, audio) — git-ignored
output/             Finished rendered videos — git-ignored
logs/               Runtime log files — git-ignored
docs/               Supporting documentation (e.g. BUSINESS_STRATEGY.md)
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

## Notes

- Secrets are never hard-coded — local secrets go in `.env` (git-ignored); CI secrets will use
  GitHub Secrets once GitHub Actions workflows are introduced.
- `assets/`, `output/`, `data/`, and `logs/` are git-ignored except for a `.gitkeep` placeholder,
  so generated media, pipeline data, and log files never get committed.
- See [CLAUDE.md](CLAUDE.md) for the full pipeline design, coding standards, and the rules that
  govern adding dependencies or API integrations.
- See [docs/BUSINESS_STRATEGY.md](docs/BUSINESS_STRATEGY.md) for the business model, revenue
  milestones, and content niche.
