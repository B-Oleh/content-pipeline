# CLAUDE.md

Persistent project instructions and architecture documentation for **content-pipeline**.
This file is the source of truth for how this repository is built and operated. Read it before making changes, and update it whenever the architecture changes.

## Mission

Build and operate an automated short-form video content production system.

The long-term objective is to minimize manual work while producing high-quality, original, platform-safe content consistently.

- **Primary target:** YouTube Shorts
- **Possible future targets:** TikTok, Instagram Reels

The current business model, revenue milestones, and content niche are documented in
[`docs/BUSINESS_STRATEGY.md`](docs/BUSINESS_STRATEGY.md) — that file is the source of truth for
business strategy. This file (CLAUDE.md) stays focused on persistent engineering instructions and
architecture; update the business strategy doc, not this section, when the model or niche change.

## Language rules

- Communicate with the user in Russian.
- The user may give instructions in Russian or English; execute either normally, but explain actions, plans, results, problems, and next steps in Russian.
- Keep all technical artifacts in English: source code, file names, terminal commands, API names, environment variables, library names, technical identifiers, comments, docstrings, configuration files, and commit messages. Do not translate these into Russian.
- Ask questions and request confirmations in Russian.

## Current development stage

The repository is currently an early scaffold. Do not assume integrations already exist — build incrementally.

The immediate goal is a stable MVP capable of producing **one complete test video from start to finish** before adding scheduling or large-scale automation.

## Priorities

When trade-offs are necessary, prioritize in this order:

1. Content quality
2. Viewer retention
3. Originality
4. Automation
5. Low operating cost
6. Reliability
7. Copyright and platform compliance

## Repository structure

[README.md](README.md) is the source of truth for the current, detailed repository structure —
do not duplicate that tree here; keep only what Claude needs architecturally:

- Pipeline automation code lives under `/scripts`; tests under `/tests`.
- `/assets`, `/output`, `/data`, and `/logs` hold generated/runtime artifacts, not source, and are
  git-ignored.
- `/state` holds persistent pipeline state (e.g. game recommendation history) that must survive
  across separate runs/workflow executions. Unlike the directories above, it is intentionally
  tracked in Git — do not add it to `.gitignore` — as the simplest $0 MVP durability mechanism
  (see docs/RESEARCH_AGENT.md "Git-backed persistent state" for why, and how it can later be
  replaced without changing pipeline business logic).
- `/docs` holds supporting documentation, including the business strategy.

Preserve this structure unless there is a good reason to change it. Do not make large architectural
changes without explaining why first. If a change adds, removes, or repurposes a top-level
directory, update README.md's structure listing in the same change.

## Architecture

The system is a linear pipeline of independent stages, each of which should be runnable and testable on its own. As the MVP matures, each stage should become its own module/script under `/scripts` with a clear input/output contract (e.g. reads a topic/script object, writes a script/asset-list object), so stages can be composed, re-run individually, and later orchestrated without rewrites (see "Cloud execution and budget" for the planned orchestration environment).

Favor plain data (JSON/YAML files or simple Python objects) passed between stages over hidden shared state, so failures in one stage are easy to isolate and re-run.

### Provider abstraction (the deliberate exception to "avoid premature abstraction")

External providers are the one deliberate exception to "avoid unnecessary generic frameworks and
speculative abstractions" (see "Coding standards"): provider availability, pricing, rate limits,
and free tiers may change, so these concerns must use thin, replaceable boundaries from the
start — never call a specific vendor's SDK directly from pipeline stage logic:

- LLM provider
- media/visual asset provider
- voice/TTS provider
- publisher (e.g. YouTube)
- analytics provider

Use the simplest interface or adapter that makes replacing a provider practical — do not build a
complex plugin framework for this. The initial LLM provider should be one with a usable free API
tier. Do not rely on the Anthropic API for the automated production pipeline itself during the $0
budget stage — this restricts what pipeline *runtime* code calls, not the use of Claude Code as
the development assistant building this repository. Introducing or switching to any paid provider
follows the "API integrations" approval rule below regardless of which concern it fills.

LLM (Gemini), visual asset (Pexels/Pixabay), voice/TTS (edge-tts), and publisher (YouTube) providers
are implemented under `scripts/production/providers/` (see docs/PRODUCTION_PIPELINE.md) — each a
small interface plus one concrete implementation, per the pattern above. The YouTube publisher
(`providers/youtube.py`) currently only uploads a video as **private** (see "Pipeline stages" below
and docs/PRODUCTION_PIPELINE.md "YouTube publishing" for why public publishing needs a separate,
not-yet-done compliance audit). The analytics provider concern is not implemented yet.

## Cloud execution and budget

The single place for cloud/CI rules — do not restate these elsewhere.

- GitHub Actions is the cloud execution environment for running the pipeline. Three manually
  triggered (`workflow_dispatch` only) workflows exist:
  `.github/workflows/research_agent.yml` (see docs/RESEARCH_AGENT.md "GitHub Actions workflow"),
  which proves Research Agent runs end-to-end and persistent state survives across runs;
  `.github/workflows/produce_video.yml` (see docs/PRODUCTION_PIPELINE.md "GitHub Actions
  workflow"), which proves one real video reaches Telegram; and
  `.github/workflows/produce_batch.yml` (see docs/BATCH_MODE.md), which delivers up to three
  candidates for morning review and commits JSON metadata under `state/production_batch/`.
  None has a schedule.
- The system must run within a strict $0 operating budget while in Stage 1 of the business
  strategy (see [`docs/BUSINESS_STRATEGY.md`](docs/BUSINESS_STRATEGY.md)). Gemini, Pexels,
  Pixabay, edge-tts, and the YouTube Data API v3 (free quota) are all used on their free
  tiers/no-cost access — see docs/PRODUCTION_PIPELINE.md for which is which.
- Any credential a GitHub Actions workflow needs is stored in GitHub Secrets, never committed
  (see "Environment variables and secret handling"). `produce_video.yml` needs
  `GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
  `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, and `YOUTUBE_REFRESH_TOKEN`; `research_agent.yml`
  needs none. `produce_video.yml` also declares
  `permissions: actions: write` (in addition to `contents: read`) so a Telegram "Regenerate" click
  can dispatch a new run of the same workflow via the GitHub REST API, using the default
  `GITHUB_TOKEN` GitHub Actions already provides to every run — not a new secret to create (see
  docs/PRODUCTION_PIPELINE.md "Telegram approval gate").
- No paid service may be enabled in CI or anywhere else without explicit user approval (see "API
  integrations") — the $0 budget is the default, not a target to negotiate down from.
- Do not add scheduling or large-scale automation until the MVP is stable (see "Current
  development stage") — the existing Research Agent workflow is deliberately `workflow_dispatch`
  only; do not add `schedule`/`cron`/`repository_dispatch` triggers to it or any other workflow
  without this restriction being revisited first.
- Favor event-driven integration (e.g. webhooks/callbacks) over polling loops for external
  services such as Telegram, especially from scheduled CI jobs, to avoid wasting execution
  minutes and to keep behavior responsive.
- Persistent pipeline state committed from a workflow (see `/state` above) must only ever be
  staged explicitly (e.g. `git add state/`) — never a blanket `git add -A`/`git add .` — so an
  automated commit can never include source code, workflow files, secrets, or other unrelated
  working-tree changes.

## Pipeline stages

The core pipeline, in order:

1. Research Agent
2. Opportunity Scoring
3. Fact Checking
4. Script Agent
5. Visual Planner
6. Asset Acquisition
7. Voice Generation
8. Video Assembly
9. Automated QA
10. Telegram Approval
11. Publishing
12. Analytics
13. Learning feedback loop

Stage 1 has a full implementation (see docs/RESEARCH_AGENT.md). Stages 4-9, plus stage 10 in full
(sending the rendered MP4 with real Approve/Regenerate/Reject buttons and actually handling a click
on one of them — see "Telegram approval gate" below for the exact mechanism and its one honest
limitation), have a working implementation under `scripts/production/` (see
docs/PRODUCTION_PIPELINE.md). Visual Planner's shot-list responsibility is folded into Script
Agent's structured Gemini output rather than a separate stage/module, since one LLM call producing
both narration and per-scene visual queries avoids a redundant second call; Asset Acquisition (stage
6) uses a cheap metadata pre-filter (`visual_relevance.py`) only to shortlist candidates, then Gemini
Vision (`vision_validation.py`) makes the actual relevance/domain decision by looking at each
shortlisted candidate's thumbnail — metadata/keyword scoring alone proved semantically unreliable in
real output (see docs/PRODUCTION_PIPELINE.md "Visual relevance"). Stages 2-3 (Opportunity Scoring,
Fact Checking) as standalone stages are not implemented as separate modules — Opportunity Scoring is
Research Agent's existing ranking (see docs/RESEARCH_AGENT.md "Ranking formula"), and Fact Checking is
currently only the automated fabrication-claim guard inside Script Agent (see
docs/PRODUCTION_PIPELINE.md "Gemini (Script Agent)"), not a fully independent verification stage.
Stage 11 (Publishing) has a first, deliberately narrow implementation: an Approve decision now
uploads the approved MP4 to YouTube via `providers/youtube.py` + `youtube_publishing.py` (see
"Telegram approval gate" below and docs/PRODUCTION_PIPELINE.md "YouTube publishing") -- but only as
**private**, since Google restricts `videos.insert` uploads from unaudited API projects to private
visibility. Public publishing is a separate, not-yet-done milestone (needs that compliance audit).
Stages 12-13 are not implemented.

Production enforces a duration-based visual gate (`production/visual_quality.py`): at least 80%
accepted Pexels/Pixabay media and at most one consecutive pure info card. Topic selection first
requires explicit PC/gaming vocabulary in title/summary. After acquisition and narration, topics
failing the gate are retried with the next eligible research candidate before rendering. QA repeats
this check before Telegram. Attempts and their populated scripts are recorded under the production
work directory; see docs/PRODUCTION_PIPELINE.md "Duration-based visual gate".


Overnight batch mode (`production/batch.py`, CLI `scripts.produce_batch`) reuses these
stages after generating a per-topic content brief. It delivers distinct candidates with
summaries, persists attempts under `state/production_batch/`, and sends a recap without
approval polling, winner selection, or publishing. See docs/BATCH_MODE.md.

### Research and opportunity scoring rules

Do not blindly generate topics. Score candidate topics using factors such as:

- audience interest
- commercial intent
- affiliate potential
- competition
- novelty
- visual potential
- retention potential
- production difficulty
- data confidence
- freshness
- evergreen value

Prefer topics supported by evidence over guesses. Persist these scores alongside each video's
later performance data (see "Analytics and learning feedback loop") so the system can eventually
determine which characteristics correlate with successful videos.

### Fact checking

This niche involves buying advice and technical claims (specs, prices, performance, compatibility).
Fact Checking is a distinct stage before scripting — do not let the Script Agent state a factual
claim (spec, price, benchmark, compatibility) that hasn't been verified. When confidence is low,
prefer flagging or dropping the claim over publishing a guess.

### Game recommendation integrity ("What to play this month")

Applies to the recurring monthly "What to play this month" content pillar (game recommendations by
PC hardware tier — see [`docs/BUSINESS_STRATEGY.md`](docs/BUSINESS_STRATEGY.md) for the business
rationale). This extends "Research and opportunity scoring rules" and "Fact checking" above:

- Do not select games based only on generic popularity. Consider: release date/current relevance,
  current player interest, review quality, hardware requirements, actual expected performance when
  reliable data is available, price, whether the game is free, availability on major PC platforms,
  visual appeal for short-form content, novelty, evergreen potential, and suitability for the
  selected hardware tier.
- Do not recommend a game that clearly does not perform acceptably on the hardware tier it's being
  recommended for.
- Never invent FPS numbers or other performance benchmarks. If reliable benchmark data isn't
  available, say so in the content rather than fabricating a specific figure — this is the same
  rule as "Fact checking" applied to performance claims, and is covered by the quality control
  checklist's "no unverified factual/technical claims" item.
- Maintain a persisted history of previously recommended games (plain data, per the "Architecture"
  principle above — not left to model memory) so future selection can deliberately balance new
  releases, older hidden gems, free games, popular games, indie titles, and AAA titles, instead of
  repeating the same games every month without a strong reason.

### Video principles

Default format:

- vertical 9:16
- primarily short-form video
- approximately 20-60 seconds unless another duration is justified

Every video should:

- establish interest immediately
- avoid unnecessary introductions
- maintain fast visual pacing
- contain meaningful visual changes
- avoid filler
- use readable, synchronized subtitles
- have clear narration
- provide genuine value or entertainment

Do not create low-effort spam or mass-produce nearly identical videos.

### Quality control checklist

Before considering a video complete, verify:

- correct resolution
- correct 9:16 aspect ratio when applicable
- audio works
- narration is intelligible
- subtitles are synchronized
- no missing media
- no obvious rendering artifacts
- no accidental watermarks
- no obviously unauthorized copyrighted assets
- no unverified factual/technical claims (see "Fact checking")
- title and description accurately represent the content

### Telegram approval gate

Telegram is the primary human-approval interface for this pipeline. Before a video may reach the
Publishing stage, the bot must eventually present:

- the rendered video
- the topic
- the proposed title
- a short explanation of why the topic was selected
- the relevant opportunity/scoring information

with three actions: ✅ Approve, 🔄 Regenerate, ❌ Reject. Public publishing must never happen
before an explicit Approve. Sending the rendered video with real inline Approve/Regenerate/Reject
buttons and a caption (topic, title, content role, selection reasoning, research score) is
implemented (see docs/PRODUCTION_PIPELINE.md "Telegram delivery"), and a click on one of them is
genuinely handled — not just displayed — by `telegram_approval.py` (see
docs/PRODUCTION_PIPELINE.md "Telegram approval gate" for the full mechanism). One documented,
deliberate limitation: GitHub Actions has no persistent webhook receiver, so callback handling is a
bounded (~20 minute) long-poll wait inside the same `produce_video.yml` run right after delivery,
not an always-on listener — this is the "favor event-driven ... over polling loops" rule's
practical limit at $0/GitHub-Actions-only, not an exception to it (the poll is bounded to one manual
run, not a recurring scheduled job, and each request blocks server-side rather than busy-looping).
A click after that window closes is not handled by that run. Regenerate re-dispatches
`produce_video.yml` for the same topic via the GitHub REST API and never uploads the
rejected/original version anywhere; Reject only records decision state
(`data/production/approval_state.json`, transient) and uploads nothing. Approve now also uploads the
exact approved MP4 to YouTube as **private** (see "Pipeline stages" above and
docs/PRODUCTION_PIPELINE.md "YouTube publishing") — idempotently, via a per-content_id publication
record (`data/production/youtube_publications.json`, transient) that prevents a second Approve press
for the same video from uploading it twice. "Current development stage" and "Human approval
required" below still apply to *public* publishing, which remains a separate, not-yet-done
milestone.

### Analytics and learning feedback loop

The system should eventually learn from published content. Preserve useful performance information such as:

- views
- watch time
- average percentage viewed
- retention
- likes
- comments
- subscribers gained
- publication date
- topic
- hook
- duration
- style

Use historical performance data to improve topic selection, hooks, scripts, pacing, visual choices, video duration, and publishing strategy.

## Coding standards

- Prefer Python for automation unless another technology has a clear advantage.
- Keep code modular and understandable — one responsibility per script/module, matching the pipeline stage it implements.
- Use meaningful names and type hints; keep functions small and focused.
- Do not replace existing working code unnecessarily.
- Avoid unnecessary dependencies (see "Adding dependencies" below).
- Avoid unnecessary generic frameworks and speculative abstractions — the pipeline is linear; don't build a generic framework before there is a second real use case for it. External providers are a deliberate, narrow exception to this — see "Provider abstraction".

## Script organization (`/scripts`)

- One script/module per pipeline stage (or a small group of tightly related stages), named after the stage it performs (e.g. `research_topics.py`, `write_script.py`, `assemble_video.py`).
- Each stage should be runnable independently from the command line for manual testing, with clear inputs (files/args) and outputs (files written to `/assets` or `/output`).
- Shared logic (config loading, logging setup, API clients, data schemas) belongs in shared modules, not copy-pasted across stage scripts.
- Stage scripts should fail loudly and clearly rather than silently producing partial/incorrect output.

## Environment variables and secret handling

- Never hard-code API keys, passwords, tokens, credentials, or secrets in code, config files, or commit messages.
- Use environment variables (a local `.env` file, not committed) for local secrets.
- Keep secrets and `.env` files out of Git — ensure they are covered by `.gitignore`.
- Never expose or transfer credentials without explicit user confirmation.
- CI/cloud credentials: see "Cloud execution and budget" (GitHub Secrets).

## Testing requirements

- Every major pipeline stage should be independently testable whenever practical — via a script entry point, a small unit test, or a manual run against sample input.
- When adding a new stage or modifying an existing one, verify it with a real (or representative sample) input/output before considering the change done.
- Investigate failures before retrying the same approach; do not repeatedly retry a failing solution without diagnosing the root cause.

## Logging requirements

- Use logging (not bare `print`) for pipeline scripts, with meaningful, actionable error messages.
- Log which stage is running, key inputs/outputs (e.g. topic chosen, file paths produced), and failures with enough context to diagnose without re-running.
- Avoid logging secrets or credentials.

## Adding dependencies

- Avoid unnecessary dependencies — prefer the standard library or an existing dependency when reasonable.
- Before adding a new dependency, make sure it earns its place (maintained, reasonably scoped, no significant overlap with something already in use).

## API integrations

Before adding a paid API or service:

1. Explain why it is needed.
2. Estimate expected cost.
3. Evaluate free alternatives.
4. Get user approval before spending money.

Enabling a paid service always requires explicit user confirmation, even after the above steps.

## Documentation requirements

- Document significant architectural changes as they happen, not after the fact.
- When the pipeline structure, repository layout, stage responsibilities, or engineering rules change, update this file (`CLAUDE.md`) in the same change so it stays the source of truth.
- When the business model, revenue strategy, or content niche changes, update `docs/BUSINESS_STRATEGY.md` instead of filling this file with business prose.
- Keep `README.md` in sync for setup/usage instructions; keep `CLAUDE.md` in sync for architecture and process.

## Development workflow

For substantial tasks:

1. Inspect the existing repository first.
2. Understand the desired final result.
3. Make a short plan.
4. Implement the solution.
5. Run relevant tests or checks.
6. Inspect the result.
7. Fix obvious problems.
8. Explain the result to the user in Russian.

Prefer producing a working implementation over only describing how it could be implemented.

## Human approval required

Always ask for confirmation before:

- publicly publishing content (once the pipeline exists, this is enforced by the Telegram
  Approval gate — see "Telegram approval gate"; until then, ask the user directly)
- spending money
- enabling a paid service
- deleting important project data
- destructive Git operations
- exposing or transferring credentials
- changing important account or security settings

Development, local testing, code generation, and non-destructive file changes may be performed autonomously when appropriate.

## Autonomous behavior on failure

When a problem occurs:

1. Investigate the cause.
2. Inspect relevant code, files, and logs.
3. Identify the likely root cause.
4. Choose the safest reasonable solution.
5. Implement it when safe.
6. Test again.
7. Document significant architectural changes.
