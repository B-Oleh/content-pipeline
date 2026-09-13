# Overnight batch mode

Run `python -m scripts.produce_batch` locally or manually dispatch **Produce Video
Batch (manual)** in GitHub Actions. Defaults: `--batch-size 3`,
`--workdir data/production`, `--output-dir output/batch`. Uses the same environment
secrets and preflight as single-video production; YouTube credentials are checked
for presence only.

One research run supplies distinct topics. Each gets an audience/pain/angle brief
with 2–3 hooks and a selected opening hook, then the existing script fabrication
guard, asset acquisition, narration, visual gate (80% accepted real-media runtime,
maximum one consecutive info card), rendering, and QA. Visual or QA failures try
the next topic; external service/tooling blockers stop the batch and preserve
already delivered candidates.

**Gemini quota coordination.** All Gemini calls for the whole batch go through one
`GeminiProvider` instance, so its shared `GeminiRateLimiter` (see
docs/PRODUCTION_PIPELINE.md "Gemini quota coordination") paces content-brief,
Script-Agent, and per-scene Vision calls against the same 20-requests-per-minute
free-tier window before they reach the server -- bursts that previously saturated
the rolling minute mid-candidate (the cause of the failed run this mode was added
for) are smoothed to a sustainable ~18.75/min. A seen 429's retry window is
published back into the shared limiter, so the next Gemini-heavy stage waits for
the window to drain rather than piling on.

**Bounded 429 recovery.** A temporary 429 that survives a single call's own retry
budget is NOT a batch blocker on first sight. `run_batch()` marks that attempt
`failed_rate_limit`, leaves any already-delivered candidates untouched, cools down
`RATE_LIMIT_RECOVERY_COOLDOWN_SECONDS` (75s) so the rate-limit window drains, and
continues with the next candidate. Only after `MAX_RATE_LIMIT_RECOVERIES` (3)
recovery rounds with no progress is a persistent 429 promoted to a real batch
blocker. This is the concrete realization of the goal's "do not treat a temporary
429 as a permanent batch blocker until a bounded recovery strategy has been
exhausted": a transient quota hit never costs the overnight run the candidates it
already delivered.

Each passing video arrives separately in Telegram with its number, topic, audience,
hook, duration, real-media coverage, and QA result. A final recap reports partial
completion or blockers. Review and reply in the morning to choose a candidate:
this mode does not poll approval buttons, select a winner, or publish. The reused
inline buttons are not processed by the batch run.

Outputs are `candidate_1.mp4`, `candidate_2.mp4`, etc. Attempt diagnostics live in
`data/production/candidates/candidate_N/`; `batch_metadata.json` records all
attempts, briefs, scripts, metrics, delivered candidates, and blockers. The same
metadata is atomically saved to `state/production_batch/batch_<timestamp>.json`
for future comparison. The manual workflow uploads diagnostics and commits only
batch-state JSON files. Local runs do not commit anything. CLI exits 0 when at
least one candidate was delivered, or 1 when none were delivered.
