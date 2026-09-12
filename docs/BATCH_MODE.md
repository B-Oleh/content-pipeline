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
