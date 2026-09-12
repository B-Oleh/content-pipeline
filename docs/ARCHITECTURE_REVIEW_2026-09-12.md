# Architecture Review — content-pipeline (pre-push evaluation)

Date: 2026-09-12
Scope: the repository as it currently sits in the working tree, reviewed as if that state were
committed and pushed to GitHub. The review evaluates the code AND docs against every stated
principle in `CLAUDE.md` and `docs/PRODUCTION_PIPELINE.md`, and calls out where the two disagree
with reality.

---

## 1. Method and evidence

- Read all production modules under `scripts/production/` (pipeline orchestration, asset
  acquisition, vision validation, visual/relevance pre-filter, info-card/hybrid rendering,
  topic selection, QA, video assembly, subtitles, voice, Telegram approval/client, YouTube
  publishing/provider, preflight, all `providers/*`).
- Read `CLAUDE.md`, `README.md`, `docs/PRODUCTION_PIPELINE.md`, `docs/BUSINESS_STRATEGY.md`
  (relevant sections), `.gitignore`, `requirements.txt`,
  `.github/workflows/produce_video.yml`, and the `.ai/` + `tools/` harness files.
- Ran the full test suite with the project's own venv:
  **`446 passed`, 9 subtests passed, 1 warning** (a `google.genai` deprecation warning on
  Python 3.14; CI pins 3.11). This includes the real-ffmpeg render integration test, the
  visual-quality/topic-retry tests, Telegram approval tests, and YouTube regression tests.

## 2. Verdict

The codebase is architecturally coherent and its own docs are mostly in sync with it. No
high-severity correctness defect was found in the reviewed code: the pipeline is linear,
fail-loud, provider-abstracted, secret-safe, free-tier-only, and covered by meaningful tests
including a genuine MP4 render+QA integration test. The gate logic (`visual_quality.py`),
the Vision-driven relevance decision, the fabrication guard, the approval gate, and private
YouTube publishing all behave as documented.

Every material issue found is **documentation / repository-hygiene / governance**, not
pipeline logic. The three that would most annoy a stranger reviewer are:

1. `README.md` still claims the pipeline has **no** Telegram approval buttons and **no**
   YouTube publishing, and lists **5** required secrets where the workflow actually needs **8**.
2. Two new top-level directories (`.ai/`, `tools/`) and `scripts/production/visual_quality.py`
   are present but **undocumented** and absent from the structural listings.
3. The `.ai/` harness contains a self-generated `VERDICT: PASS` and a `GOAL.md` that calls the
   cycle a "read-only health check" while the reviewed diff modifies production code — an
   inconsistent audit trail that should not be mistaken for independent review.

Recommendation: fix the docs gaps (a few small, low-risk edits), decide the fate of `.ai/`+`tools/`
(documented harness or removed before the push), and push.

## 3. Findings

### F-1 (High — docs/reality mismatch) README is materially out of date

`README.md` line 19 still reads:

> …No YouTube publishing, scheduling, analytics, or Telegram approval buttons exist yet.

Both of the two named "missing" features are implemented in this repo (`telegram_approval.py`,
`providers/youtube.py`, `youtube_publishing.py`, and `pipeline.py::_handle_approve`). The same
section's prerequisite list (lines ~205–209) states "all 5 secrets — `GEMINI_API_KEY`,
`PEXELS_API_KEY`, `PIXABAY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`", but
`produce_video.yml` also wires `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`,
`YOUTUBE_REFRESH_TOKEN`, and drives a Regenerate click with `GITHUB_TOKEN`. A reader following
README would be told the approval gate and YouTube upload don't exist, then be missing secrets
when the run asks for them.

CLAUDE.md's documentation rule ("Keep `README.md` in sync for setup/usage instructions") is the
governing expectation, and this commit violates it. CLAUDE.md itself is in sync (it documents
both features correctly), which makes the README gap look like an oversight rather than a design
decision.

**Fix:** update the intro paragraph, the prerequisite lists (both "Running locally" and "Running on
GitHub Actions"), and the brief pipeline description to reflect the Telegram approval gate and the
private YouTube upload on Approve.

### F-2 (Medium — structure/docs) `.ai/` and `tools/` are undocumented additions

The working tree contains two new top-level directories neither `README.md`'s structure listing
nor `CLAUDE.md` mention: `.ai/` (the FrameForge loop state: `GOAL.md`, `POLICY.md`, `STATUS.json`,
`REVIEW.md`, `IMPLEMENTATION.md`) and `tools/` (`agent_loop.sh`, an orchestrator that drives a
`codex exec` implementer and a `claude -p` reviewer in rounds).

Consequences for a committed push:

- CLAUDE.md's rule "If a change adds … a top-level directory, update `README.md`'s structure
  listing in the same change" is violated for both.
- `tools/agent_loop.sh` is a real, executable façade into the build process (it can run the test
  gate, rewrite `.ai/STATUS.json`, and block on reviewer verdicts). Shipping it with zero
  documentation means the next maintainer cannot tell whether `.ai/` is live automation, a
  one-off scratch pad, or junk.
- `.ai/REVIEW.md` and `.ai/IMPLEMENTATION.md` are snapshots of one past cycle (the gate work),
  not live guidance; `STATUS.json` records `goal_status: complete`. Treating these as current
  state on a committed repo will mislead.

This is not a code defect, but a stranger reviewer would flag an undocumented build/orchestration
surface and two unlisted directories on arrival.

**Fix:** either (a) document the harness (add it to README's structure listing and a short
"Autonomous agent loop" section), or (b) keep `.ai/`/`tools/` out of the push (gitignore or a
separate branch/tool). Don't push them silently.

### F-3 (Medium — governance) `.ai/GOAL.md` contradicts the reviewed diff, and the PASS is self-generated

`.ai/GOAL.md` declares: "Read the repository and perform a read-only health check. **Do not modify
production code.**" Yet `.ai/REVIEW.md` (which passed) reviews a diff that **adds**
`visual_quality.py` and **modifies** `topic_selection.py`, `asset_acquisition.py`, `pipeline.py`,
`qa.py`, `video_assembly.py`, and their tests — i.e. production code. Either the GOAL belongs to an
older cycle and was not updated, or the loop ran past its own constraint; either way the audit
trail is internally inconsistent.

Separately, the `VERDICT: PASS` in `.ai/REVIEW.md` is produced by the same harness that wrote the
code (`claude -p` invoked by `tools/agent_loop.sh`). It is a machine-generated checkpoint, not an
independent review, and should not be cited as third-party validation of the gate change.

**Fix:** keep the goal file in sync with what a cycle actually does (or delete it with each
completed cycle), and make `REVIEW.md`'s provenance explicit if it is committed.

### F-4 (Low — docs) PRODUCTION_PIPELINE.md "Module map" lists a stale tree

The "Module map" section documents each production module but omits `scripts/production/
visual_quality.py` (the new duration-based gate that CLAUDE.md now references) and never mentions
`visual_attempts.json`/`attempts/` in the data-flow or map. The prose ("Duration-based visual
gate") is accurate and in sync; only the map/list is incomplete.

**Fix:** add `visual_quality.py` to the module map and mention the attempt-recording files in the
data-flow section.

### F-5 (Low — resilience/cost) Topic-retry loop spends a full Gemini Script+Vision pass per candidate

`pipeline.py`'s retry loop (and its `else`-raise) is correct, but each retried candidate re-runs
`generate_script` (≥1 Gemini call) plus a full `acquire_assets` pass (up to
`SHORTLIST_SIZE`=2 Vision calls **per scene**). On Gemini's free tier (a real run hit the
20-request limit — documented), two or three gate-failing topics can consume the run's whole
quota before a passable topic is reached, at which point 429 retries just delay a failure.

This is an acknowledged design trade-off (the gate must measure *after* real durations exist, so a
cheaper pre-check isn't available), and `SHORTLIST_SIZE=2` already bounds the Vision cost. Flagged
as a risk to track now that retries are on the critical path, not a defect to fix immediately.

**Possible later mitigation:** a metadata-only/goal-scored pre-filter to order candidates
already exists (`estimate_visual_producibility`); consider also short-circuiting the retry order
by that signal before spending another full Script pass.

## 4. Principle-by-principle compliance

### CLAUDE.md

| Principle | Verdict | Evidence / notes |
|---|---|---|
| Language rules (Russian to user, English artifacts) | PASS | Code, docs, and this review are English; user-facing comms Russian. |
| Current development stage (MVP: one complete test video; no scheduling) | PASS | `produce_video.yml` is `workflow_dispatch`-only; no `schedule`/`cron`; integration test proves a real render. |
| Priorities (quality→retention→originality→automation→cost→reliability→compliance) | PASS | Quality gates, Vision-relevance honesty, fabrication guard, private-only publish reflect the stated order. |
| Repository structure + "update README on top-level changes" | **FAIL (F-2)** | `.ai/`, `tools/` unlisted; otherwise `state/` tracked, runtime dirs ignored as documented. |
| Provider abstraction (deliberate exception) | PASS | `providers/{llm,visual,voice,youtube}.py` interfaces; stage code never calls vendor SDKs; Telegram client isolated; analytics not yet implemented (documented). |
| Cloud execution and $0 budget; no scheduling until MVP stable | PASS | All providers free-tier; secrets via GitHub Secrets; no paid services; the 20-min bounded approval poll is documented as the practical limit of the "prefer webhooks/polling" rule. |
| Pipeline stages (4–10 + private slice of 11) | PASS | Stages match the docs; Visual Planner folded into Script Agent, Opportunity Scoring = research ranking, Fact Checking = fabrication guard — all documented. |
| Research/opportunity scoring rules | PASS | `select_topic_candidate` reuses research ranking; visual-reliability tiering is a documented re-prioritization, not a re-scoring. |
| Fact checking ("no unverified claims") | PASS | `find_fabrication_risks` (FPS/price/% regex) + corrective retry + loud failure; prompt grounds claims in evidence. |
| Game recommendation integrity | PASS | Monthly-pillar logic lives in Research Agent (reused unchanged); production pipeline intentionally does not touch `state/` — documented. On-topic gate prevents off-niche regeneration overrides. |
| Video principles (9:16, ~20–60s, hook, pacing, subtitles, no spam) | PASS | Schema enforces 4–6 scenes/90–115 words (~30–45s); QA enforces 1080×1920, 15–90s, ≥3 scenes; subtitles wrapped to ≤2 lines. |
| Quality control checklist | PASS (with caveats) | Mechanically-checkable items covered by QA/gates; "intelligible", "no artifacts", "no watermarks" depend on sourcing (Vision rejection, no rejected assets, no watermark-laden sources expected). |
| Telegram approval gate | PASS | Real Approve/Regenerate/Reject handling, bounded long-poll, honest failure reporting, documented 20-min limitation and `--poll-only` recovery. |
| Analytics/learning loop | PASS (not yet, documented) | Stages 12–13 unimplemented; per-video/scores persistence scaffolding exists. |
| Coding standards / script organization | PASS | One responsibility per module; typed; CLI-runnable; shared logic in providers/utils; no speculative framework. |
| Secrets handling | PASS | Env-only; no hard-coding; `.env` ignored; sanitized error paths (Pexels URL-leak, Telegram base-URL, YouTube OAuth) verified by tests. |
| Testing requirements | PASS | 446 passing; real-ffmpeg integration; no-network unit tests via fakes; failure paths (VisualQualityError before render/deliver) covered. |
| Logging requirements | PASS | `logging` throughout; stage/failures logged; no secrets in logs. |
| Dependency discipline | PASS | `requirements.txt` justifies each dep (requests, google-genai, edge-tts, Pillow, google-auth, google-api-python-client) and its free tier. |
| API integrations / paid services | PASS | No paid service; YouTube is the free Data API quota; payout to public publishing (compliance audit) explicitly deferred. |
| Documentation requirements | **FAIL (F-1, F-4)** | CLAUDE.md and PRODUCTION_PIPELINE.md prose in sync; README stale; module map incomplete. |
| Human approval required | PASS | Public publishing never auto; uploads private-only; Approve is the required gate. |
| Autonomous failure handling | PASS | Root-cause documented per incident (quota 429, .ass sizing, drawtext wrap, zoompan ordering); retries bounded; loud failures. |

### docs/PRODUCTION_PIPELINE.md

| Section | Verdict | Notes |
|---|---|---|
| Goal/non-goals | PASS | Scope matches code (incl. "no PUBLIC publishing" and "no topic-history updates"). |
| Module map | **FAIL (F-4)** | Missing `visual_quality.py`; otherwise accurate. |
| Data flow | PASS | Matches `pipeline.py`; attempt-recording not displayed but described in the gate section. |
| Topic selection | PASS | Text matches `topic_selection.py`, incl. the new `is_on_topic` gate and `preferred_title` override behavior. |
| Gemini (Script Agent) / quota resilience / fabrication guard | PASS | Matches `providers/llm.py` and `script_agent.py` (Interactions API, `response_format` schema, 429-only retry honoring Retry-After/RetryInfo/parsed sentence; language about free-tier quota). |
| Gemini Vision / Visuals / production modes / info card / hybrid | PASS | Matches `vision_validation.py`, `asset_acquisition.py`, `info_card.py`; the zoompan-before-overlay ordering gotcha is documented and independently explained in code. |
| Duration-based visual gate | PASS | Matches `visual_quality.py` (80% runtime coverage, ≤1 consecutive card, fail-closed on invalid durations/empty list, rejected assets never downloaded). |
| Voice / Subtitles / Video / QA | PASS | Per-scene TTS duration, `.ass` styling, `trim/atrim` alignment, deterministic ffprobe checks all match code. |
| Telegram approval gate / YouTube publishing / Secrets / Workflow | PASS | Mechanism and one honest limitation documented; private-only upload + idempotency record match code; GitHub Actions wiring verified in `produce_video.yml`. |
| Testing | PASS | Test inventory described matches what exists and what I ran. |

## 5. What was verified by execution (not just reading)

- `pytest` → **446 passed**, 9 subtests, 1 harmless deprecation warning.
- The visual-gate unit tests (`test_visual_quality.py`) confirm the exact 80% boundary, the 1-card
  streak cap, fail-closed behavior on `None`/0/negative/NaN/inf durations and on empty lists, and
  that hybrid/photo/accepted-real media count while generated/rejected/missing/unknown media do not.
- `test_pipeline_visual_quality.py` confirms topic retry happens **before** render/delivery, that a
  passing topic still renders/delivers, and that `visual_attempts.json` is written.
- The real-render integration test passes through `render_video` → `run_qa` and asserts
  resolution, duration, timeline match, real-media coverage, and info-card streak.

## 6. Strengths worth preserving (so future work doesn't regress them)

- **Fail-closed honesty everywhere:** rejected visuals are never used (not even as blurred
  backdrops), fabricated claims fail the script, QA failure blocks Telegram, off-topic topics are
  rejected before spend, and a durationless/invalid scene fails the gate.
- **Cost-shaping without skimping:** metadata pre-filter → 2-candidate Vision shortlist → one
  cache per run; presence-only preflight for Gemini/YouTube; 429-only retry with server-honored
  backoff.
- **Doc-driven architecture:** PRODUCTION_PIPELINE.md is unusually accurate against the code and
  records the real incidents (paper-card/grocery-shelf Vision failures, `.ass` sizing, drawtext
  wrap, zoompan ordering) that drove each design decision. This is exactly the "document as you
  go, not after" standard CLAUDE.md asks for.
- **Provider boundaries that will actually hold:** stage code calls only interfaces; secrets are
  prevented from leaking at the highest-risk seams (Pixabay query-param key, Telegram token in
  base URL, YouTube OAuth in request strings).

## 7. Pre-push checklist

1. [ ] F-1: update `README.md` (intro claims, local + Actions secret lists).
2. [ ] F-4: add `visual_quality.py` to the module map / data-flow in `PRODUCTION_PIPELINE.md`.
3. [ ] F-2: decide `.ai/` + `tools/` — document them in README or exclude them from the push.
4. [ ] F-3: align `.ai/GOAL.md` content with the actual cycle scope, or drop it before push.
5. [ ] Confirm no secrets are staged (the diagnostic artifact and `data/` are ignored; `state/` is
      tracked intentionally and is the only auto-committed path).
6. [ ] Consider F-5 (retry cost) if free-tier 429s recur in production runs.

Closing note: none of the above blocks shipping the code. The pipeline does what its docs promise;
the gaps are that the docs' *claims about what exists* (README) and *catalog of what exists*
(module map, top-level structure) haven't caught up with the last two features.