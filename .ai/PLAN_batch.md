# Implementation Plan: Overnight batch of 3 distinct video candidates (FrameForge)

Claude (supervisor) defines this spec. Codex (implementer) implements it, then Claude reviews.

## Objective

Produce up to **3 distinct, high-quality video candidates in one batch run**, each delivered to
Telegram separately with a concise per-candidate summary. No public publishing. No auto-selected
winner. Persist batch metadata for future comparison.

Reuses the existing single-video pipeline (`scripts/production/*`) unchanged; adds a batch mode
that shares the same stage functions. `scripts/production/pipeline.py` and
`scripts/produce_video.py` must NOT be modified (existing behavior preserved; their tests stay
green).

## New/changed files

1. `scripts/production/content_brief.py` (new) — ContentBrief dataclass + generation.
2. `scripts/production/providers/llm.py` (edit) — add `CONTENT_BRIEF_SCHEMA`,
   `build_content_brief_prompt()`, `parse_content_brief_response()`, `to_content_brief()`, and a new
   provider method `generate_content_brief(prompt) -> str` on `LlmProvider` + `GeminiProvider`.
   Also extend `build_script_prompt(..., content_brief=None)` and add `ContentBrief` import-free
   handling (see detail below).
3. `scripts/production/script_agent.py` (edit) — `generate_script(provider, scored_candidate,
   content_brief: ContentBrief | None = None)`; pass the brief into `build_script_prompt` on both
   the first attempt AND the fabrication-adjacency retry.
4. `scripts/production/visual_quality.py` (edit) — extract `media_coverage_fraction(scenes) -> float`
   used by `check_visual_quality` (pure refactor, numeric result). Idempotent/additive.
5. `scripts/production/telegram_delivery.py` (edit) — add `build_batch_caption(...)`; extend
   `deliver_video(video_path, script, scored_candidate, client, caption: str | None = None)`.
6. `scripts/production/batch.py` (new) — `run_batch()` orchestrator + result dataclasses.
7. `scripts/produce_batch.py` (new) — CLI entry point.
8. `.github/workflows/produce_batch.yml` (new) — `workflow_dispatch`-only batch workflow that also
   commits git-backed batch metadata under `state/production_batch/` (explicit `git add
   state/production_batch/`, JSON-only guard, no force push).
9. `scripts/config.py` — no change needed (`STATE_DIR` already exists).
10. Tests (new/edited): `tests/production/test_content_brief.py`,
    `tests/production/test_batch.py`, `tests/production/test_telegram_delivery.py` (extend),
    `tests/production/test_script_agent.py` (extend for `content_brief` passthrough),
    `tests/production/test_gemini_provider.py` (extend for `generate_content_brief` request shape),
    `tests/production/test_visual_quality.py` (extend for `media_coverage_fraction`).
11. Docs: `docs/BATCH_MODE.md` (or a section in `docs/PRODUCTION_PIPELINE.md`) describing batch mode.
    Also update `README.md` (brief usage line) and `CLAUDE.md` (pipeline structure / architecture
    notes) only to the extent the batch flow is part of the architecture. Keep doc changes minimal
    and consistent.

## 1. Content brief

### `scripts/production/content_brief.py`

```python
@dataclass
class ContentBrief:
    target_audience: str
    viewer_pain: str
    topic_angle: str
    hook_candidates: list[str]   # 2-3 short opening-line candidates
    selected_hook: str           # must equal one of hook_candidates
    hook_rationale: str          # why the selected hook retains attention
    def to_dict(self): ...
    @classmethod from_dict(cls, data): ...
```

Functions (all in content_brief.py, mirroring script_agent.py's structure):

- `generate_content_brief(provider: LlmProvider, scored_candidate: ScoredCandidate) -> ContentBrief`
  - Builds prompt via `llm.build_content_brief_prompt(...)`.
  - `raw_text = provider.generate_content_brief(prompt)` (NEW provider method).
  - `data = llm.parse_content_brief_response(raw_text)`.
  - `return llm.to_content_brief(data, ...)` (validates shape; raises `ScriptGenerationError` on
    malformed/missing fields or if `hook_candidates` < 2).
- No fabrication guard is applied to the brief (a hook may mention a number); the fabrication guard
  already runs later over the *script* text in `script_agent.generate_script`.

### `scripts/production/providers/llm.py` additions

- `CONTENT_BRIEF_SCHEMA` (JSON schema dict):
  ```json
  {"type":"object","properties":{
    "target_audience":{"type":"string"},
    "viewer_pain":{"type":"string"},
    "topic_angle":{"type":"string"},
    "hook_candidates":{"type":"array","items":{"type":"string"},"minItems":2,"maxItems":3},
    "selected_hook":{"type":"string"},
    "hook_rationale":{"type":"string"}},
   "required":["target_audience","viewer_pain","topic_angle","hook_candidates","selected_hook","hook_rationale"]}
  ```
- `build_content_brief_prompt(*, title, content_pillar, content_role, monetization_path, summary,
  hardware_tier, game_title, scoring_reasoning, evidence) -> str` — pure function (same signature
  shape as `build_script_prompt`). Instructs the model to return a concise YouTube-Shorts content
  brief for this exact topic in the PC-gaming/hardware niche: target audience, the viewer
  pain/problem this video solves, a fresh/non-generic topic angle, **2-3 hook candidates** (each a
  single short opening sentence that earns attention in the first 2 seconds), the **selected_hook**
  (must be one of the candidates), and why that hook should retain attention. Say hooks may be punchy
  but must NEVER fabricate a number/price/spec not present in the evidence.
- `parse_content_brief_response(raw_text, *, retry_note="") -> dict` — mirrors
  `parse_script_response`: strips markdown fence, `json.loads`, raises `ScriptGenerationError` on
  invalid JSON or missing required keys.
- `to_content_brief(data, *, title: str) -> ContentBrief` — builds the dataclass; trims strings;
  normalizes `hook_candidates` to 2-3 entries (if >3 truncate; if <2 raise `ScriptGenerationError`);
  sets `selected_hook = str(data["selected_hook"]).strip()`; validates `selected_hook` is one of
  `hook_candidates` (case-insensitive equality) and if not, raise `ScriptGenerationError` (a decoded
  hook not present among candidates is a malformed brief; fail loudly).
- `LlmProvider.generate_content_brief(self, prompt: str) -> str` — interface method, default
  `raise NotImplementedError`.
- `GeminiProvider.generate_content_brief(self, prompt: str) -> str` — identical to
  `generate_script` but with `schema=CONTENT_BRIEF_SCHEMA`; wrap in `_call_with_retry`, read
  `output_text`, raise `ScriptGenerationError` if empty.
- `build_script_prompt(..., content_brief: "ContentBrief | None" = None)` — new optional keyword
  argument. When provided, inject a block near the top of the context:
  ```
  Content brief (design to it):
  - Target audience: {target_audience}
  - Viewer pain/problem: {viewer_pain}
  - Topic angle: {topic_angle}
  - Hook candidates: {hook_candidates}
  - Selected opening hook (YOU MUST use this verbatim as your ``hook`` field): {selected_hook}
  - Why this hook: {hook_rationale}
  ```
  and add one behavioral sentence to the rules: "Your `hook` field MUST be the selected opening hook
  from the content brief, verbatim. If the selected hook is not already honest to the evidence, use
  it anyway as the opening line (it is an attention device; the fabrication guard separately checks
  the narration for invented numbers)."
  Do NOT let the brief change any other existing rule (scene count, visual queries, no-fabrication,
  honesty about generic footage).
- Import `ContentBrief` inside `llm.py` lazily (import at top of the functions that need it, or use
  `TYPE_CHECKING`) to avoid a circular import (`content_brief.py` imports from `providers.llm.py`).
  Preferred: in `llm.py`, add `from __future__ import annotations` (already present) and refer to
  `ContentBrief` only under `if TYPE_CHECKING:`; runtime code only uses `content_brief.hook_candidates`
  etc. via duck typing, or accept `content_brief` as `Any`. Keep it simple: treat `content_brief` as
  an optional object with attributes; no runtime import needed.

### `scripts/production/script_agent.py`

- `generate_script(provider: LlmProvider, scored_candidate: ScoredCandidate, content_brief:
  Optional["ContentBrief"] = None) -> VideoScript`.
- Pass `content_brief=content_brief` into both `build_script_prompt` calls (initial and the
  fabrication-adjacency retry).

## 2. Visual quality numeric helper

`scripts/production/visual_quality.py`:

- `def media_coverage_fraction(scenes: list[Scene]) -> float` — compute `real/total` with the SAME
  rule `check_visual_quality` uses (accepted real_visual/hybrid_visual Pexels/Pixabay media,
  non-zero existing files, duration-weighted; `0.0` if no valid total).
- Refactor `check_visual_quality` to call it so the rule lives in ONE place. Do not change its
  returned `QAResult` shape/values.

## 3. Batch Telegram caption + delivery

`scripts/production/telegram_delivery.py`:

- `def build_batch_caption(script, scored_candidate, *, candidate_number, batch_size,
  content_brief, duration_seconds: float, real_media_coverage: float, qa_passed: bool) -> str`.
  Returns a string beginning with the concise summary block (requirement 10), then a blank line,
  then the existing `build_caption(script, scored_candidate)` output for the extended detail. The
  summary block lines:
  ```
  🎬 Candidate {candidate_number}/{batch_size}
  🎮 Topic: {script.topic}
  👥 Target audience: {content_brief.target_audience}
  🪝 Hook: {content_brief.selected_hook}
  ⏱ Duration: {duration_seconds:.1f}s
  🎞 Real-media coverage: {real_media_coverage:.0%}
  ✅ QA: passed
  ```
  (or `❌ QA: failed` when not passed). Keep emoji exactly `🎬 🎮 👥 🪝 ⏱ 🎞 ✅ ❌`.
- `deliver_video(video_path, script, scored_candidate, client, caption: str | None = None)` — when
  `caption` is provided, use it instead of `build_caption(...)`; identical send + keyboard + id
  checks otherwise.

## 4. Batch orchestrator

`scripts/production/batch.py`:

```
BATCH_SIZE = 3
```

Dataclasses:

```python
@dataclass
class BatchAttemptResult:          # one candidate attempt (delivered or not)
    attempt_number: int
    candidate_id: str
    topic: str
    candidate_number: Optional[int]   # set when successfully delivered
    status: str                       # "delivered" | "failed_visual" | "failed_qa" | "failed_exception"
    failure_reason: Optional[str]     # for non-delivered
    content_brief: Optional[ContentBrief]  # if the brief was generated
    script: Optional[VideoScript]     # if a script was generated (to_dict form? keep object)
    qa_passed: Optional[bool]
    duration_seconds: Optional[float]
    real_media_coverage: Optional[float]
    video_path: Optional[str]
    def to_dict(self): ...
```

```python
@dataclass
class BatchResult:
    batch_id: str                     # ISO-8601 UTC timestamp, e.g. "2026-09-13T00:30:00Z"
    generated_at: str
    batch_size: int
    attempts: list[BatchAttemptResult]
    completed: list[BatchAttemptResult]   # the delivered ones, in delivery order
    blocker: Optional[str]
    def to_dict(self): ...
```

`run_batch(*, gemini_api_key, pexels_api_key, pixabay_api_key, telegram_bot_token,
telegram_chat_id, workdir, output_dir, research_config_path=None, research_state_dir=None,
batch_size=BATCH_SIZE) -> BatchResult`

Behavior, in order:

1. `workdir = Path(workdir).mkdir(parents=True, exist_ok=True)`;
   `output_dir = Path(output_dir).mkdir(parents=True, exist_ok=True)`.
2. `research_result = run_research_agent(config_path=research_config_path,
   state_dir=research_state_dir)` — ONE research run for the whole batch (passed through like
   `pipeline.py` does: only include kwargs whose value is not None).
3. `remaining = list(research_result.candidates)`. `llm_provider = GeminiProvider(gemini_api_key)`;
   `visual_providers = [PexelsProvider(pexels_api_key), PixabayProvider(pixabay_api_key)]`;
   `voice_provider = EdgeTtsProvider()`; `telegram_client = TelegramClient(telegram_bot_token,
   telegram_chat_id)`.
4. Loop while `len(delivered) < batch_size` and `remaining` and `blocker is None`:
   - `scored = select_topic_candidate(replace(research_result, candidates=remaining))`; remove it
     from `remaining`. `NoSuitableCandidateError` → record an attempt with
     `status="failed_exception"`, `failure_reason="no on-topic candidate remaining"`; set
     `blocker = "Research produced no more on-topic candidates to reach batch_size"`; break.
   - `attempt_number += 1`; `attempt_dir = workdir / "candidates" / f"candidate_{attempt_number}"`;
     mkdir. Wrap the whole per-candidate body in `try/except Exception as exc`.
   - Body (mirroring pipeline.py stage order):
     1. `brief = generate_content_brief(llm_provider, scored)`
     2. `script = generate_script(llm_provider, scored, content_brief=brief)`
     3. `acquire_assets(script.scenes, visual_providers, attempt_dir, llm_provider)`
     4. `subtitle_cues = generate_narration(script.scenes, voice_provider, attempt_dir)`
     5. `(attempt_dir/"script.json").write_text(json.dumps(script.to_dict(), indent=2,
        ensure_ascii=False), encoding="utf-8")`
     6. `quality = check_visual_quality(script.scenes)`; if not `quality.passed`, record attempt
        `status="failed_visual"`, `failure_reason="; ".join(quality.summary_lines())`, `continue`
        (requirement 7: try the next candidate).
     7. `subtitle_path = attempt_dir/"captions.ass"; write_ass(subtitle_cues, subtitle_path)`
     8. `video_path = render_video(script.scenes, output_dir / f"candidate_{len(delivered)+1}.mp4",
        subtitle_path=subtitle_path)`
     9. `qa_result = run_qa(video_path, rendered_scene_count=len(script.scenes),
        narration_generated=all(scene.audio_path is not None for scene in script.scenes),
        subtitles_generated=len(subtitle_cues) > 0, scenes=script.scenes)`;
        `(attempt_dir/"qa_result.json").write_text(...)`
     10. If `not qa_result.passed`: record attempt `status="failed_qa"`, reason from
         `qa_result.summary_lines()`, `continue`.
     11. Delivery: `capture = build_batch_caption(script, scored, candidate_number=len(delivered)+1,
         batch_size=batch_size, content_brief=brief, duration_seconds=sum(s.duration_seconds or 0
         for s in script.scenes), real_media_coverage=media_coverage_fraction(script.scenes),
         qa_passed=qa_result.passed)`; `deliver_video(video_path, script, scored, telegram_client,
         caption=capture)`. Record delivered attempt with status `"delivered"`.
   - `except Exception as exc`:
     - If this is an **external blocker** (see `_is_external_blocker`), set `blocker = str(exc)`,
       record an attempt `status="failed_exception"`, `failure_reason=<sanitized>`, and `break`.
     - Else record attempt `status="failed_exception"`, `failure_reason=<sanitized>`, and
       `continue` to the next candidate.
5. After the loop: if `delivered < batch_size` and `blocker is None`, set
   `blocker = "produced {len(delivered)}/{batch_size} candidate(s); research candidates exhausted
   before reaching batch_size"`.
6. Persist metadata:
   - `runner_meta = BatchResult(...)`
   - `(workdir / "batch_metadata.json").write_text(json.dumps(runner_meta.to_dict(), indent=2,
     ensure_ascii=False), encoding="utf-8")` (transient, uploaded as artifact by the workflow).
   - `persist_batch_metadata(runner_meta, state_dir)` where `state_dir` defaults to
     `STATE_DIR / "production_batch"` (from scripts.config). Write `state_dir / f"batch_{batch_id
     (sanitized)}.json"` via `scripts.utils.atomic_write.atomic_write_text`. Write the same JSON.
     This is git-tracked persistent state so future batches can compare attempted topics/hooks
     (requirement 14). Sanitize `batch_id` to `[A-Za-z0-9._-]` for the filename.
7. Telegram recap (requirement 12: no winner chosen): if at least one candidate delivered, send one
   additional `telegram_client.send_message(...)`:
   ```
   📦 Batch complete: {n}/{batch_size} candidate(s) delivered overnight.
   Review each video above and reply in the morning to pick which to publish.
   (No candidate was auto-selected.)
   ```
   Plus, if `blocker` is set, a `⚠️ {blocker}` line. Keep it short. If `n == 0`, still send a single
   message `⚠️ Batch produced no deliverable candidates.\nReason: {blocker or "unknown"}`.

`_is_external_blocker(exc: Exception) -> bool` convenience — returns True when the exception is:

- a Gemini rate-limit/quota exhaustion (reuse `_is_rate_limited` from
  `scripts.production.providers.llm`), OR
- any `google.genai` API error (e.g. auth) — check `type(exc).__module__.startswith("google.genai")`,
  OR
- a `requests` `ConnectionError`/`Timeout`/`HTTPError`, OR
- a `VoiceGenerationError` (external TTS), OR
- an `AssetAcquisitionError`, OR
- an `ffmpeg`/subprocess `VideoAssemblyError` (tooling/environment failure),
- else False.

Logging: use `scripts.utils.logging_utils.get_logger`; log each stage transition and each attempt's
outcome; never log secrets. Use `preflight._safe_error_message(exc)` (import from
`scripts.production.preflight`) for any logged exception string.

## 5. CLI `scripts/produce_batch.py`

Mirror `scripts/produce_video.py`:

- `argparse`: `--workdir` (default `DATA_DIR/"production"`), `--output-dir` (default
  `OUTPUT_DIR/"batch"`), `--batch-size` (int, default 3).
- Read the same env vars as produce_video plus `STATE` handled by code defaults.
- `run_preflight(...)` with the same required/optional secrets (gemini, pexels, pixabay, telegram,
  youtube presence makes preflight pass). On `PreflightError`, log + `sys.exit(1)`.
- Import `scripts.production.batch.run_batch` and call it.
- Print a final human summary: number delivered, each delivered candidate's number/topic/hook/
  coverage/duration/QA, and any blocker. Exit code: `0` if at least one candidate delivered;
  `1` if none (so CI shows a clear failure when the batch produced nothing).
- Docstring documents that this is the overnight batch mode; no approval polling here
  (requirements: deliver candidates for morning review; no automatic publishing).

## 6. Workflow `.github/workflows/produce_batch.yml`

- `name: Produce Video Batch (manual)`.
- `on: workflow_dispatch: {}` — NO schedule/cron/repository_dispatch (CLAUDE.md "Cloud execution and
  budget").
- `permissions: contents: write` (to push the git-backed batch-metadata JSON) — no `actions: write`
  needed (batch does not dispatch regeneration runs). Keep the concurrency guard
  `group: produce-video-batch`, `cancel-in-progress: false`.
- `timeout-minutes: 60`.
- `env`: same secrets as produce_video.yml: `GEMINI_API_KEY`, `PEXELS_API_KEY`,
  `PIXABAY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `YOUTUBE_CLIENT_ID`,
  `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` (YouTube secrets are presence-only-required by
  preflight; the batch itself never uploads).
- Steps (mirroring produce_video.yml + research_agent.yml's state-commit pattern):
  1. Checkout.
  2. Set up Python 3.11.
  3. Install dependencies.
  4. Ensure ffmpeg + DejaVu font.
  5. `pytest` (full suite).
  6. Preflight step: `python -m scripts.production.preflight`.
  7. Run batch: `python -m scripts.produce_batch`.
  8. Upload diagnostics artifact on failure (or always): `data/production/`, `output/`, `logs/`.
  9. Detect changes under `state/production_batch/` → guard: ONLY `.json` files may appear under
     that path → `git add state/production_batch/` → commit
     `chore(state): update production batch metadata` → push `HEAD:${github.ref_name}` (no force).

## 7. Tests (Codex adds these; run full suite before reporting)

- `tests/production/test_content_brief.py`:
  - `build_content_brief_prompt` includes topic, pillar, and the instruction to return 2-3 hooks.
  - `parse_content_brief_response` accepts valid JSON, strips a markdown fence, raises on bad JSON /
    missing field.
  - `to_content_brief` normalizes hook_candidates to 2-3; raises if <2; raises if selected_hook not
    among candidates.
  - `generate_content_brief` calls the provider's new method and returns a ContentBrief (fake
    provider).
- `tests/production/test_batch.py` (monkeypatch everything like
  `test_pipeline_visual_quality.py` does; fake providers):
  - Delivers exactly `batch_size` distinct topics (assert candidate_ids all distinct, topics
    distinct) when every candidate succeeds; `batch_metadata.json` exists under workdir AND under
    state_dir; Telegram `send_video` called once per delivered candidate; a recap `send_message`
    call happens; output video files named `candidate_1.mp4`..`candidate_N.mp4`.
  - Visual-gate failure on the first candidate → retries the next candidate (°req 7); failed attempt
    recorded with `status="failed_visual"`.
  - QA failure → recorded `failed_qa`, next candidate retried.
  - External blocker after one success (e.g. a fake provider raising a `_is_external_blocker`-ish
    error) → keeps the 1 delivered, sets `blocker`, stops (req 8).
  - Candidates exhausted below batch_size → `blocker` set; whatever was delivered is kept.
  - No public publishing: assert no YouTube provider is ever constructed/uploaded by batch code.
- `tests/production/test_telegram_delivery.py`:
  - `build_batch_caption` contains all required fields: Candidate number/total, Topic, Target
    audience, Hook (selected), Duration, Real-media coverage %, QA result, plus base caption content.
- `tests/production/test_script_agent.py`:
  - `generate_script(..., content_brief=brief)` results in a prompt that includes the selected hook
    and the content-brief block (fake provider records the prompt).
  - The fabrication-adjacency retry path also passes the brief (unchanged prompt block).
- `tests/production/test_gemini_provider.py`:
  - `generate_content_brief` returns the parsed output_text and its request uses
    `CONTENT_BRIEF_SCHEMA` (validate request shape against the real SDK's request model exactly like
    the existing `generate_script` test does).
- `tests/production/test_visual_quality.py`:
  - `media_coverage_fraction` returns the same value the gate derives; handles empty/absent
    durations as 0.

## 8. Validation gate (shared, requirement 6)

Reuse the EXISTING gate functions — do not write new quality logic:

- `check_visual_quality(scenes)` (>=80% real-media runtime coverage, <=1 consecutive info card).
- `qa.run_qa(...)` (resolution, streams, duration, scene count, narration/subtitles).
- `script_agent` fabrication guard (no invented FPS/price/percentage).
- `topic_selection.is_on_topic` gate already applied inside `select_topic_candidate`.

A candidate is only "delivered" if all of the above pass.

## 9. Non-goals (do not implement)

- No public YouTube publishing (batch never uploads; no approval polling in batch).
- No scheduling/cron.
- No analytics, no background music, no TikTok/Reels.
- No changes to `scripts/production/pipeline.py`, `scripts/produce_video.py`,
  `.github/workflows/produce_video.yml`, telegram_approval.py, providers/youtube.py, info_card.py.
- No speculative framework or generic batch engine.

## 10. Definition of done (for Codex)

- Full test suite `./.venv/bin/python -m pytest -q` passes (existing 446 + new tests).
- `git diff --check` clean.
- Batch CLI runs with mocked providers in tests; no network in unit tests.
- Codex reports exactly which files it changed/added and which tests it ran.