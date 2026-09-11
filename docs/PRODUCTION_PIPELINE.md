# Video Production Pipeline

First end-to-end milestone that turns one Research Agent topic into a real rendered vertical MP4
delivered to Telegram with real, actually-handled Approve/Regenerate/Reject buttons. Covers
CLAUDE.md pipeline stages 4-10 (Script Agent, Visual Planner, Asset Acquisition, Voice Generation,
Video Assembly, Automated QA, Telegram approval gate — see "Telegram approval gate" below). Stage 1
(Research Agent) is reused unchanged — see [docs/RESEARCH_AGENT.md](RESEARCH_AGENT.md).

Engineering rules (secrets, dependencies, logging, testing, provider abstraction) are defined once
in CLAUDE.md and are not repeated here.

## Goal and non-goals

The goal of this milestone is narrow and concrete: **one manual GitHub Actions run produces one
real MP4, delivers it to Telegram with working Approve/Regenerate/Reject buttons, and actually
handles a click on one of them.** Everything here is built to reach that, and no further:

- No YouTube publishing yet -- Approve only records state as a clean integration point (see
  "Telegram approval gate" below).
- No scheduling/cron.
- No analytics or affiliate automation.
- No background music (narration-only audio for the first working pipeline).
- No topic-history/game-history updates from this pipeline (see "Relationship to Research Agent
  state" below) -- that stays exclusively Research Agent's own concern.

## Module map

```
scripts/produce_video.py              CLI entry point (python -m scripts.produce_video)
scripts/production/
  models.py                            Scene, VideoScript, SubtitleCue, QAResult
  preflight.py                         Verifies all 5 secrets/services before spending any time
                                        on research/rendering; also its own CLI (python -m
                                        scripts.production.preflight) for a separate workflow step
  topic_selection.py                   Picks one Research Agent candidate, preferring pillars that
                                        are reliably illustrated with generic stock footage
  script_agent.py                      Gemini call + the fabrication-claim guard (see below)
  asset_acquisition.py                 Per-scene multi-candidate Pexels/Pixabay search, metadata
                                        shortlist, Gemini Vision validation, info-card fallback
                                        (see "Visuals" below)
  visual_relevance.py                  Deterministic metadata-only relevance PRE-FILTER + shot-type
                                        classification -- builds the shortlist, does not decide
  vision_validation.py                 Gemini Vision: the actual accept/reject relevance decision
                                        over each scene's metadata shortlist (see "Visuals" below)
  info_card.py                         Renders a designed text/graphic "information card" clip when
                                        no honestly-matching stock asset exists
  voice_generation.py                  Per-scene edge-tts narration; scene duration = its own audio
  subtitles.py                         Groups word timings into short caption chunks; writes .ass
  video_assembly.py                    ffmpeg: scale/crop to 1080x1920, concat scenes, burn subtitles
  qa.py                                ffprobe-based deterministic checks before Telegram delivery
  telegram_delivery.py                 Builds the caption + approval keyboard, sends the MP4
  telegram_approval.py                 Real Approve/Regenerate/Reject handling: keyboard, callback
                                        parsing, bounded long-poll wait, regeneration dispatch (see
                                        "Telegram approval gate" below)
  pipeline.py                          Orchestrates all of the above, in order
  ffmpeg_utils.py                      Shared ffprobe/ffmpeg-binary helpers
  providers/
    llm.py                             LlmProvider interface + GeminiProvider (script text + Vision)
    visual.py                          VisualAssetProvider interface + PexelsProvider/PixabayProvider
    voice.py                           VoiceProvider interface + EdgeTtsProvider
    telegram_client.py                 Telegram Bot API client: sendVideo (with buttons), getUpdates,
                                        answerCallbackQuery, sendMessage
.github/workflows/produce_video.yml   Manually triggered (workflow_dispatch only) full run
```

Every provider module follows CLAUDE.md's "Provider abstraction" exception: stage modules
(`script_agent.py`, `asset_acquisition.py`, `voice_generation.py`, `telegram_delivery.py`) only
ever call the small interface in `providers/`, never a vendor SDK/response shape directly.

## Data flow

```
Research Agent (scripts/research/cli.py::run(), reused as-is, in-memory only -- see below)
  -> ResearchResult
topic_selection.select_topic_candidate() -> one ScoredCandidate
script_agent.generate_script() -> VideoScript (topic, hook, scenes[], title, description, evidence)
asset_acquisition.acquire_assets() -> mutates each Scene with a downloaded (or info-card-rendered)
                                       asset_path (see "Visuals" below)
voice_generation.generate_narration() -> mutates each Scene with audio_path + duration_seconds;
                                          returns global SubtitleCue list
subtitles.write_ass() -> captions.ass
video_assembly.render_video() -> output/final_video.mp4 (1080x1920, H.264, AAC, burned subtitles)
qa.run_qa() -> QAResult
  if passed: telegram_delivery.deliver_video() -> Telegram (video + Approve/Regenerate/Reject buttons)
             telegram_approval.poll_for_decision() -> ApprovalState (bounded wait -- see below)
             REGENERATE -> telegram_approval.trigger_regeneration_workflow() -> new produce_video.yml run
  if failed: pipeline stops, nothing is sent (see "QA" below)
```

Intermediate files (downloaded assets, per-scene audio, `captions.ass`, `script.json`,
`qa_result.json`, `approval_state.json`) live under `data/production/` -- transient, gitignored,
exactly like Research Agent's own `data/research/` (see docs/RESEARCH_AGENT.md "Persistent state vs
transient output").
The final video is written to `output/final_video.mp4` per this milestone's explicit requirement;
`output/` is also gitignored (see CLAUDE.md "Repository structure") -- **the video is never
committed to Git.**

## Relationship to Research Agent state

This pipeline calls `scripts.research.cli.run()` directly (the pure research-and-rank function),
not `main()` -- so it does **not** write `data/research/` output or update
`state/research/game_history.json`. That stays exclusively the concern of the existing, separate
`research_agent.yml` workflow. Keeping this pipeline read-only with respect to the repository is
deliberate: it means `produce_video.yml` needs no write permission and cannot conflict with
`research_agent.yml`'s own state commits (see docs/RESEARCH_AGENT.md "Concurrency"). A future
milestone could feed the produced video's chosen topic back into game history if repeat-topic
avoidance across video runs turns out to matter in practice -- not implemented now.

## Topic selection

`topic_selection.py` reuses Research Agent's own ranking (`ScoredCandidate.overall_score`) as the
base signal and only re-prioritizes among top candidates for **visual feasibility** for this first
real run:

| Pillar | Visual reliability |
|---|---|
| optimization, mistakes_and_myths, gaming_technology | High -- generic tech/PC b-roll is plentiful on stock sites |
| buying_advice | Medium |
| hardware_comparison | Low -- often names exact products stock libraries won't have |
| game_recommendations, monthly_games | Lowest -- need a *specific* game's footage |

If no candidate falls in a reliable pillar, the single best-ranked candidate is used anyway (with a
logged warning) rather than failing the run -- CLAUDE.md priorities (see "Priorities") were not
asked to be relaxed, but a hard stop here would defeat the milestone's purpose of proving delivery.

`select_topic_candidate()` also takes an optional `preferred_title` -- set from the `TOPIC_OVERRIDE`
environment variable, in turn set by produce_video.yml's `topic_override` workflow_dispatch input
when a run was started by a Telegram "Regenerate" click (see "Telegram approval gate" below). If a
candidate with that exact title exists in this run's fresh Research Agent results, it is selected
directly; if it is no longer present (Research Agent re-runs from scratch each time, so an identical
candidate set is not guaranteed), selection falls back to the normal logic above with a logged
warning rather than failing the run.

## Gemini (Script Agent)

`providers/llm.py::GeminiProvider` uses the official `google-genai` SDK's **Interactions API**
(`client.interactions.create(...)`, `gemini-3.6-flash` by default via the single
`DEFAULT_GEMINI_MODEL` constant) -- not `client.models.generate_content`. That older path's
automatic function calling (AFC) machinery fired even for a plain text-only prompt with no tools
configured, which could return an empty response and get misreported as a generic
`ScriptGenerationError` with no indication anything AFC-related was involved. Interactions has no
client-side AFC concept: tools are explicit, opt-in server-side declarations that this provider
never passes, so `ping()` (preflight) and `generate_script()` (below) both have a call shape you
can literally inspect for the absence of a `tools`/`response_format` key (see
`tests/production/test_gemini_provider.py`).

Structured output goes through `response_format={"type": "text", "mime_type": "application/json",
"schema": SCRIPT_JSON_SCHEMA}` (a plain JSON Schema dict) -- enforced server-side, on top of (not
instead of) the prompt's own JSON-shape instructions, since a schema alone can't express semantic
rules like scene count or word-count targets. `"schema"` is the key the current official
Interactions API documentation specifies and the key the SDK actually serializes onto the wire
regardless of input (confirmed against the installed `google-genai` SDK's own request-validation
model in `tests/production/test_gemini_provider.py`, not just this codebase's own tests). The
prompt (`build_script_prompt`) embeds the candidate's
title/pillar/role/monetization path/summary, the Research Agent scoring reasoning, and every
evidence item from `raw_metadata["evidence"]` (see docs/RESEARCH_AGENT.md "Evidence") -- the model
is instructed to ground claims in that evidence and never invent specifics beyond it.

**Gemini preflight does not call Gemini.** `preflight.py::_check_gemini()` only checks that
`GEMINI_API_KEY` is a non-blank string -- it deliberately does **not** call `GeminiProvider.ping()`
(which still exists, and still works exactly as described below, for any caller that wants a
schema-free connectivity check) or any other Gemini endpoint. `script_agent.generate_script()`
makes a real Gemini call moments after preflight passes; a separate live probe in preflight would
burn one of Gemini's free-tier request-quota units for nothing before the pipeline has done any
real work -- exactly what pushed a real run over Gemini's 20-request free-tier quota (HTTP 429
"Quota exceeded ... generate_content_free_tier_requests, limit: 20"). The first real
`generate_script()` call now serves as the actual connectivity test instead (see "Gemini quota
resilience" below for what happens if that call itself hits a 429).

`GeminiProvider.ping()` (unused by preflight, kept as a general-purpose schema-free connectivity
check) sends nothing beyond `model` and a fixed prompt ("Reply exactly with OK") -- no tools, no
response schema -- specifically so a plain connectivity/auth failure is never confused with a
structured-output or tool-calling problem. `ping()` and `generate_script()` both read
`interaction.output_text` (the SDK's own "concatenated text from the last model output" convenience
field); if it is empty, the failure reason comes from the interaction's own `status` and
`errors[].message` (both provider-supplied, safe to log in full -- see
`_describe_incomplete_interaction`), not a guess. A ping failure raises `GeminiPingError`; a
script-generation failure raises `ScriptGenerationError` -- two distinct exception types, so
nothing collapses a connectivity problem and a script validation problem into the same reported
type. `preflight.py::_safe_error_message` (still used by the Pexels/Pixabay/Telegram checks, which
remain live probes) always names the underlying exception's class alongside whatever safe message
is available, instead of collapsing every failure into one indistinguishable string.

**Gemini quota resilience.** `providers/llm.py::_call_with_retry()` wraps every
`interactions.create(...)` call (`generate_script()`, `evaluate_visual_candidate()`, and `ping()`)
and retries ONLY on an HTTP 429 (`_is_rate_limited()`). Up to `GEMINI_MAX_RETRIES` (3) retries,
honoring the server's own `Retry-After` header, Google's structured `RetryInfo.retryDelay` detail,
or (as a sanitized last resort) Gemini's own "Please retry in N.NNNNNNNNNs." sentence parsed
straight out of the error message, whichever is present (`_extract_retry_after_seconds()`) --
otherwise exponential backoff (`GEMINI_BASE_RETRY_DELAY_SECONDS` doubling each attempt, capped at
`GEMINI_MAX_RETRY_DELAY_SECONDS`) plus up to 25% jitter -- logged as `"Gemini rate limited, retrying
in N seconds"` (never including request/response bodies or the API key). Any other exception (auth
failure, malformed response, a non-429 HTTP error) is not retried and propagates immediately,
unchanged from before.

`_is_rate_limited()` checks, in priority order: (1) the installed SDK's own
`google.genai._gaos.lib.compat_errors.RateLimitError` type, imported lazily and best-effort; (2)
`status_code == 429`; (3) `code == 429` (an older SDK shape, kept for compatibility); (4) a nested
`response.status_code` or a parsed error body/detail whose own `code` field is 429; (5) a sanitized
fallback match on `"error code: 429"` / `"too_many_requests"` in the error's own message text. This
priority list exists because a real GitHub Actions run proved the original single `code == 429`
check silently never matches the exception the installed google-genai SDK (2.22.0) actually raises
for a 429: `RateLimitError` (a subclass of `APIStatusError`) carries `status_code`, not `code`, and
has no `.code` attribute at all -- so the retry path never engaged and the run failed immediately on
the first rate-limit hit. `_extract_retry_after_seconds()` follows the same "structured first,
sanitized text last" philosophy: it checks `Retry-After` and `RetryInfo.retryDelay` on whichever of
`.details`/`.body` the installed SDK happens to populate, and only if neither is present parses the
delay directly out of the error's own message text (supports decimal seconds, e.g. "Please retry in
24.354420322s." -> `24.354420322`) -- exactly the shape Gemini's real quota-exceeded message uses,
since this SDK version does not expose that delay as a separate structured field at all.

This turns one transient rate-limit hit into a short wait instead of an immediate pipeline failure;
it does not change what happens once the retry budget is exhausted -- that still raises loudly (see
the per-caller exception types above), except inside `vision_validation.py`'s per-candidate
try/except, where an exhausted retry on one candidate is already treated as a rejection of that
candidate, not a whole-scene failure (see
"Visuals" below).

**Fabrication guard.** The prompt instructs Gemini never to state a specific FPS number, price,
exact spec, release date, popularity statistic, or performance percentage unless it is in the
evidence. On top of that instruction, `find_fabrication_risks()` runs a small regex safety net over
the generated hook/narration/on-screen text for the three sharpest, least-ambiguous fabricable
"hard numbers": FPS figures, prices, and performance percentages. It deliberately does **not** try
to catch a bare year or generic claim, since this niche's own topics legitimately mention years
(e.g. "...in 2026") and a broader guard would be too noisy to trust. On a hit, `script_agent.py`
retries once with a corrective follow-up prompt quoting the exact violation; if a violation
survives that retry, script generation fails loudly (`ScriptGenerationError`) rather than sending
an unverified claim to production -- this is the automated version of CLAUDE.md's "Fact checking":
"When confidence is low, prefer flagging or dropping the claim over publishing a guess."

The hook is spoken over scene 0's visual (merged into that scene's `narration_line` for TTS/timing
purposes) rather than treated as its own scene; `VideoScript.hook` still holds it separately for
the Telegram caption.

## Gemini Vision (visual relevance)

`GeminiProvider.evaluate_visual_candidate(image_bytes, mime_type, prompt)` -- used by
`vision_validation.py`, see "Visuals" below for the calling logic -- is a second, multimodal use of
the same Interactions API `generate_script()` uses, not a separate SDK or vendor. The one real
mechanical wrinkle: `client.interactions.create(input=...)` accepts a plain string, a `Content`
object/list, or a list of `Step` objects, but a bare list of content dicts
(`[{"type": "text", ...}, {"type": "image", ...}]`) is silently misinterpreted by the installed
`google-genai` SDK as a list of unrecognized "steps," not as message content -- it must be wrapped as
one `{"type": "user_input", "content": [...]}` step. This was confirmed directly against the
installed SDK's own request-validation model (`google.genai.interactions.CreateModelInteraction`,
the same technique `test_generate_script_response_format_validates_against_the_real_sdk_request_model`
already used for `generate_script()`'s `response_format` key) before being relied on --
see `tests/production/test_gemini_provider.py::test_evaluate_visual_candidate_request_validates_against_the_real_sdk_request_model`.
Image bytes travel base64-encoded in the `data` field with an explicit `mime_type`; the response
goes through the same `response_format={"schema": ...}` mechanism as `generate_script()`, with its
own `VISION_EVALUATION_SCHEMA` (`computer_domain`, `scene_relevance_score`, `misleading`, `reason`).
A missing/empty response raises `VisionEvaluationError` (distinct from `ScriptGenerationError`/
`GeminiPingError`, same reasoning as those two: a Vision-call failure should never be reported or
handled as if it were a different kind of Gemini failure).

## Visuals

`providers/visual.py` implements `PexelsProvider` and `PixabayProvider` against their official,
documented, free-tier APIs (video search first, photo search as fallback) -- no scraping.

**Query generation.** Script Agent's prompt (`providers/llm.py::_SCRIPT_JSON_INSTRUCTIONS`) requires
3-5 CONCRETE, specific `visual_search_queries` per scene -- e.g. "gaming laptop keyboard close up",
"desktop gpu inside pc case", "pc game performance settings menu" -- rather than vague single-word
queries like "gaming" or "computer" that match almost anything. The prompt also asks for shot-type
variety within a scene's own query list (close-up / hardware-detail / monitor-UI / person-use-case)
and across scenes, and forbids naming a specific game/product/model in any query, since stock
libraries will not have that exact footage (`SCRIPT_JSON_SCHEMA` enforces 3-5 items server-side on
top of the prompt instruction).

**Multi-candidate collection and metadata pre-filter.** `asset_acquisition.py` no longer stops at
the first search hit. For each scene it queries every one of its `visual_search_queries` against
BOTH providers' video search (falling back to photo search only if no video candidate was found at
all), stopping early once `MIN_CANDIDATE_POOL_SIZE` (6) candidates have been collected to stay
within free-tier rate limits. Every candidate is first scored by
`visual_relevance.py::score_candidate()` -- metadata-only (search query, the result's own page-URL
slug, width/height; no image download) -- then deduplicated by `_dedupe_candidates()` (the same
underlying asset returned by more than one search query collapses to its single
highest-metadata-scored occurrence, so overlapping queries can never fill the Vision shortlist with
copies of one asset), and only the top `SHORTLIST_SIZE` (2) candidates, best metadata score first,
are kept. `SHORTLIST_SIZE` was reduced from 3 to 2 (and this dedup step added) after a real GitHub
Actions run hit Gemini's free-tier request quota (HTTP 429 "Quota exceeded ...
generate_content_free_tier_requests, limit: 20") -- see "Gemini quota resilience" above.

**This metadata score is a pre-filter only, not the relevance decision.** An earlier version of this
pipeline used it as the final decision (the "deterministic metadata/query scoring fallback"), and
real production output showed exactly why that is insufficient: a paper greeting card was selected
for a "graphics card" narration scene (both share the word "card"), and a grocery-store shelf was
selected for a "discounts" scene (both matched generic sale-adjacent keywords). Keyword/URL overlap
has no notion of what is actually depicted in an image. The metadata score still rewards specific
(non-generic) keyword overlap, a closer-to-9:16 aspect-ratio bonus, and a soft shot-type-repetition
penalty for variety (`classify_shot_type()`: close_up / hardware_detail / monitor_ui /
person_use_case / environment_setup) -- it is simply demoted to "cheap first-pass ranking signal,"
per the task's own explicit allowance for this.

**Gemini Vision makes the final decision.** `vision_validation.py::select_vision_validated_candidate()`
takes the metadata shortlist and, for EVERY candidate in it (not stopping at the first one that
passes), downloads only a small static preview/thumbnail (`AssetResult.thumbnail_url` -- Pexels
video's `image` field, Pexels photo's `src.small`, Pixabay video's `videos.<size>.thumbnail`,
Pixabay photo's `previewURL`; never the full video/photo file at this stage) and sends it to Gemini
alongside the scene's narration, intent, search query, and an explicit content-domain anchor ("The
content domain is PC gaming, gaming hardware, computer components, gaming technology, or game
recommendations.") via `GeminiProvider.evaluate_visual_candidate()` (a multimodal Interactions API
call -- see "Gemini Vision (visual relevance)" below). Gemini returns `{"computer_domain": bool,
"scene_relevance_score": 0-100, "misleading": bool, "reason": str}`. A candidate passes only if
`computer_domain` is true, `misleading` is false, AND `scene_relevance_score >=
VISION_RELEVANCE_THRESHOLD` (70); among every candidate that passes, the one with the **highest**
`scene_relevance_score` is selected -- a 72 never wins over a 94 just because it was evaluated first.
This still bounds Gemini Vision calls to at most `SHORTLIST_SIZE` (2) per scene (per the task's
"only call Vision for the final 2-3 candidates per scene" requirement) rather than every raw search
hit -- the shortlist size controls cost, not an early-exit. A candidate with no `thumbnail_url`, or
one whose Vision call itself fails (network, malformed response, or an exhausted 429 retry budget --
see "Gemini quota resilience" above), is treated as rejected rather than crashing the scene's search
or silently falling back to the metadata score.

`acquire_assets()` also threads one `vision_cache` dict through every scene in the run, so the exact
same thumbnail+context (image URL, scene narration, on-screen text, and query -- everything that
actually determines Vision's answer, see `vision_validation.py::_vision_cache_key()`) is never sent
to Gemini twice within one pipeline run. This is deliberately scoped to an exact match rather than
thumbnail alone: the same image can genuinely be relevant to one scene and not another, so caching
purely by thumbnail across different scenes would risk reusing a judgment that no longer applies --
which CLAUDE.md's "Quality control checklist" and this task's own "do not change output quality
rules" both rule out. In practice this cache mostly catches the rarer case of two otherwise-distinct
candidates sharing one CDN thumbnail URL; the far more common overlapping-query duplicate is already
removed earlier, for free, by `_dedupe_candidates()` above.

**Fallback: information card, with NO rejected asset in it.** If every shortlisted candidate is
rejected (or none was found at all), `asset_acquisition.py` renders a designed **information card**
(`info_card.py::render_info_card()`) with a plain flat designed background -- instead of using
misleading generic footage -- honoring the task's explicit "never imply generic stock footage is
footage of a specific named game, GPU, laptop, or product" rule, and its explicit "do not use the
best bad candidate" instruction. A rejected candidate is **never downloaded and never appears
anywhere in the rendered video** -- not as scene footage, not as a photo, and not as an
information-card background (darkened/blurred or otherwise): a grocery-store shelf rejected for a
PC-hardware-discount scene must never appear in that scene in any form, so `_use_info_card()` does
not attempt any backdrop download at all. The card shows a short headline (the scene's
`on_screen_text`, or the first sentence of its narration) and, when available, a supporting fact
line, with a subtle zoom/pan over its flat background. Scenes filled this way are marked
`asset_source = "info_card"` so it is possible to audit, from `script.json` alone, how many scenes
in a given video used a real Vision-approved asset vs. a generated card.

A still photo (a genuinely Vision-approved one) gets a mild continuous zoom (`zoompan` in
`video_assembly.py` / `info_card.py`) so no scene sits completely static.

**Secret-safety note:** Pixabay's API key travels as a `key=` URL query parameter (no header option
in their public API). A naive `response.raise_for_status()` would embed the full request URL --
including the key -- in the exception message. `providers/visual.py::_raise_for_status()` reports
only the HTTP status code and response body, never the request URL, specifically to avoid that leak
(see "Secrets" below). The same reasoning applies to `providers/telegram_client.py`, whose base URL
contains the bot token -- every request there is wrapped so a raw `requests` exception (whose
string form typically includes the URL) never propagates or gets logged as-is.

## Voice (edge-tts)

`providers/voice.py::EdgeTtsProvider` uses `edge_tts.Communicate(..., boundary="WordBoundary")` --
free, no API key, per this milestone's explicit "no paid TTS" requirement. Each **scene** is
synthesized separately (not one call for the whole script): a scene's narration audio determines
that scene's `duration_seconds` (measured via `ffprobe`, not the last word-boundary timestamp, so
the video segment length always exactly matches its audio segment length -- see
`voice_generation.py`). This is what keeps video and audio in sync end to end without a separate
alignment pass, and is why there are no silent gaps: the narration track covers 100% of the
runtime by construction.

## Subtitles

`subtitles.py::group_words_into_cues()` groups the word-level timings edge-tts provides into short
caption chunks: at most 6 words or ~3 seconds per cue, breaking early at a true sentence end
(`.`/`!`/`?` -- not a comma, which produced awkward one-word captions in testing). `time_offset`
shifts each scene's word timings onto the whole video's timeline, so one global subtitle file covers
the entire runtime.

**Why `.ass`, not `.srt`.** The first working pipeline wrote plain `.srt` and burned it in via
ffmpeg's `subtitles` filter with a `force_style` + `original_size` combination intended to control
font size and margins. In real rendered output this produced massively oversized captions (single
words filling most of the frame height). Controlled experiments (comparing renders with/without
`original_size`, byte-for-byte) showed `original_size` had no effect at all in this
ffmpeg/libass build: the `subtitles` filter's internal SRT-to-ASS auto-conversion uses its own
reference resolution, independent of both the real video resolution and the `original_size` option,
so `force_style` font-size/margin values were being scaled against the wrong reference. The fix was
to stop relying on that auto-conversion entirely: `subtitles.py::write_ass()` now writes a
hand-authored `.ass` file with an explicit `[Script Info]` `PlayResX: 1080` / `PlayResY: 1920`
header and a fully-specified `[V4+ Styles]` line, so libass has no ambiguous reference resolution to
guess. `video_assembly.py::_build_subtitle_filter()` now just passes the `.ass` path through with no
`force_style` at all -- every visual property lives in the file itself.

**Chunking, not just font size.** Per the task's "fix the chunking logic rather than only shrinking
the font" instruction, `wrap_cue_text()` guarantees every cue wraps to at most `MAX_LINES_PER_CUE`
(2) lines by construction (splitting on an estimated max-characters-per-line for the configured font
size/frame width, never emitting a 3rd line), on top of `group_words_into_cues()`'s existing
`MAX_WORDS_PER_CUE` (6) and per-cue max-duration (3s) limits -- so a cue is never a long paragraph
block. Current style constants (`subtitles.py`): `FONT_SIZE_PX = 58` (within the requested 54-64px
range), `HORIZONTAL_MARGIN_PX = 100`, `BOTTOM_MARGIN_PX = 200` (lower-third, not flush to the bottom
edge), `BorderStyle=1` outline+shadow (not an opaque box) for readability over any background,
bottom-center alignment. `group_words_into_cues()` also guarantees non-overlapping, strictly
sequential cue timing (covered by tests -- see "Testing").

## Video (ffmpeg)

`video_assembly.py` builds one `filter_complex`: each scene's visual is looped (video) or held
(photo) and trimmed to exactly that scene's narration duration, scaled+cropped to cover 1080x1920,
paired with that scene's own audio, and all scene pairs are joined with the `concat` filter
(`v=1:a=1`) before the subtitle burn-in. Output: H.264 (`libx264`, `yuv420p`), AAC audio (`aac`,
128k), `+faststart` for fast preview playback, written to `output/final_video.mp4`. No background
music in this first working pipeline (see "Non-goals" above).

## QA

`qa.py::run_qa()` runs deterministic `ffprobe`-based checks before Telegram delivery is even
attempted: file exists and is non-empty, `ffprobe` can read it, has a video stream, has an audio
stream, resolution is exactly 1080x1920, duration is 15-90 seconds (target 30-45s with margin for
real variance), at least 3 scenes were rendered, and narration/subtitle generation both reported
success. **If any check fails, `pipeline.py` does not call Telegram at all** -- no partial/failed
video is ever delivered as if it were a success (see CLAUDE.md "Quality control checklist" and
"Stage scripts should fail loudly").

## Telegram approval gate

`providers/telegram_client.py` is a Bot API client: `getMe()` for preflight, `sendVideo()` (now
accepting an optional `reply_markup` for the inline keyboard) for delivery, `sendMessage()` for
confirmations, `getUpdates()` for receiving callback clicks, and `answerCallbackQuery()` to clear a
button's client-side "loading" spinner. The caption (`telegram_delivery.py::build_caption()`)
includes the topic, proposed title, content role, why it was selected (Research Agent's own scoring
reasoning), the overall research score, and a note on how many scoring dimensions are backed by
real evidence vs heuristic (see docs/RESEARCH_AGENT.md "Heuristic vs real data").
`deliver_video()` attaches `telegram_approval.py::build_approval_keyboard(content_id)` -- one row of
✅ Approve / 🔄 Regenerate / ❌ Reject, each button's `callback_data` shaped `action:content_id`.
`content_id` is `VideoScript.candidate_id` (populated from `ResearchCandidate.candidate_id`, required
and non-empty), so a later decision always maps back to the exact generated video; `deliver_video()`
raises loudly if it is somehow unset rather than sending unmappable buttons.

**Why polling from inside the same GitHub Actions run, not a webhook.** A Telegram button click only
reaches a bot via a webhook (an always-listening HTTPS endpoint Telegram calls instantly) or long
polling (`getUpdates`). GitHub Actions runners are ephemeral -- they exist only for one job's
duration -- so there is nowhere for a webhook to be received once the job ends; standing up a
persistent webhook receiver would mean a new always-on hosting service outside this repository's
existing $0/GitHub-Actions architecture, which neither CLAUDE.md "Cloud execution and budget" nor
this task's own instructions allow without that being explicitly revisited first. The mechanism this
task explicitly asked for instead -- "the smallest practical callback-handling mechanism compatible
with the current GitHub Actions architecture" -- is what `telegram_approval.py::poll_for_decision()`
does: right after delivery, the **same** workflow run long-polls Telegram's own `getUpdates` endpoint
in a bounded loop (`DEFAULT_POLL_TIMEOUT_SECONDS` = 1200s / 20 minutes). Each `getUpdates` call blocks
server-side for up to 25s waiting for a new update before returning, so the whole window costs on the
order of ~48 HTTP requests, not a busy loop -- this is the practical $0/GitHub-Actions-only limit of
CLAUDE.md's "favor event-driven ... over polling loops" rule, not an exception to it: the wait is
bounded to one manual (`workflow_dispatch`) run, never a recurring scheduled job.

**The one honest limitation:** a button click that happens *after* the 20-minute window closes (the
workflow run has already ended) is not handled by that run -- there is no persistent listener at
$0/GitHub-Actions-only to catch it later. Every callback received during the window is answered
(`answerCallbackQuery`, clearing the spinner) even if it is malformed or addressed to a different/
stale `content_id`, so a user's tap is never left visibly hanging; only a match for the current
`content_id` ends the wait and is acted on.

**What each decision actually does** (`pipeline.py::_wait_for_approval`):
- **✅ Approve** -- records `ApprovalState(decision="approve")` to `data/production/approval_state.json`
  (transient, gitignored, same place as `script.json`) and sends the Telegram confirmation
  "✅ Approved". This is the clean integration point for a future YouTube publisher (Stage 11): a
  publisher stage can read this file and act only on `decision == "approve"`, without
  `telegram_approval.py` knowing anything about YouTube. YouTube publishing itself is not
  implemented.
- **🔄 Regenerate** -- acknowledges the callback, sends "🔄 Regeneration started", and calls
  `telegram_approval.py::trigger_regeneration_workflow()`, which dispatches a **new**
  `produce_video.yml` run via the GitHub REST API
  (`POST /repos/{repo}/actions/workflows/produce_video.yml/dispatches`), passing the current
  `script.topic` as the `topic_override` input so `topic_selection.py` prefers re-selecting the same
  topic (see "Topic selection" above) -- this is "the same topic/content intent" the task asks for,
  without a second workflow or new infrastructure. Uses the run's own default `GITHUB_TOKEN`:
  `workflow_dispatch` is an explicit, documented exception to GitHub's "events triggered by
  GITHUB_TOKEN do not start new workflow runs" recursion guard, so no separate personal access token
  is needed -- only `permissions: actions: write` on the workflow (see "GitHub Actions workflow"
  below). If `GITHUB_TOKEN`/`GITHUB_REPOSITORY` are unavailable (e.g. running locally) or the
  dispatch call fails, this is reported honestly (a Telegram message explaining regeneration could
  not be triggered automatically, and the recorded `ApprovalState.detail`) rather than silently
  claiming success -- see the task's explicit "do not silently fall back to non-functional buttons"
  rule.
- **❌ Reject** -- records `ApprovalState(decision="reject")` and sends "❌ Rejected". No
  regeneration, no publishing.
- **Timeout** (no decision within the window) -- records `ApprovalState(decision="timeout")`; the
  video and its buttons remain in the Telegram chat, but this run takes no further action.

## Secrets

`GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` are
read from the environment only (`os.getenv`, GitHub Secrets in CI / `.env` locally) -- never
hard-coded, never logged. `preflight.py` reports only presence/absence and pass/fail per service,
using a provider's own safe error field when available (e.g. `google.genai.errors.APIError.message`,
which comes from Gemini's own JSON error body) or just the exception's type name otherwise -- never
a raw exception string, which for a lower-level transport error can include request details. See
"Visuals" above for the two specific URL-based leak risks (Pixabay, Telegram) and how they're
closed.

Two more environment variables are read only for the "Regenerate" button (see "Telegram approval
gate" above): `GITHUB_TOKEN` (the workflow's own default per-run token -- not a new secret to
create) and `GITHUB_REPOSITORY` (set automatically by GitHub Actions on every runner). Neither is
required locally; regeneration is simply reported as unavailable if they are absent (see that
section's "do not silently fall back to non-functional buttons" handling), rather than the pipeline
failing outright.

## GitHub Actions workflow

`.github/workflows/produce_video.yml` -- manually triggered only (`workflow_dispatch`, with one
optional `topic_override` input set automatically by a "Regenerate" click, see "Telegram approval
gate" above; no `schedule`/`cron`/`repository_dispatch`). Steps: checkout -> Python 3.11 -> install
dependencies -> ensure ffmpeg AND a text-rendering font are installed (checks first, `apt-get
install` only if missing -- `info_card.py`'s `drawtext` filter needs a real font *file*: it does not
rely on fontconfig resolving a font by name, since a broken/missing fontconfig config was observed
locally on Windows; `fonts-dejavu-core` guarantees one of `info_card.py`'s documented font paths
exists on the Ubuntu runner) -> run the full automated test suite -> preflight (`python -m
scripts.production.preflight`, its own step so a credential problem fails clearly and immediately)
-> `python -m scripts.produce_video` (research through the bounded Telegram approval wait).
`permissions: contents: read` (this workflow never commits or pushes -- see "Relationship to
Research Agent state" above) plus `permissions: actions: write` (so a "Regenerate" click can
dispatch a new run of this same workflow -- see "Telegram approval gate" above). `timeout-minutes:
45` (raised from 30 to comfortably fit the ~20-minute bounded approval wait on top of rendering).
`GITHUB_TOKEN` is passed into the job's `env` from `secrets.GITHUB_TOKEN` (the default per-run token
GitHub Actions already provides -- not a new secret to create) purely so `os.getenv("GITHUB_TOKEN")`
can see it; `GITHUB_REPOSITORY` needs no such wiring, since GitHub Actions sets it as a default
environment variable on every runner automatically. On failure, `data/production/`, `output/`, and
`logs/` are uploaded as a short-retention (3 day) diagnostic artifact -- never treated as a
publishing target, and containing no secrets (none of this pipeline's code ever writes a secret
value to disk or a log line).

This is a second, independent workflow alongside `research_agent.yml` (see
docs/RESEARCH_AGENT.md "GitHub Actions workflow") -- they do not share a concurrency group, since
one only ever reads the repository and the other only ever touches `state/`.

## Testing

Per this milestone's explicit instruction ("the most important test is: can this pipeline create a
real MP4?"), the centerpiece test is
`tests/production/test_video_assembly_integration.py`: it renders a real MP4 from ffmpeg-generated
synthetic scenes (`lavfi` color/tone sources, no network, no API keys) through the actual
`video_assembly.py` + `qa.py` code and asserts QA passes -- this is the one test that most directly
answers that question, and it needs only ffmpeg (skips gracefully if ffmpeg/ffprobe are not on
PATH). `tests/production/test_info_card.py` and the info-card-fallback cases in
`tests/production/test_asset_acquisition.py` follow the same real-ffmpeg-with-graceful-skip pattern,
since both genuinely render an MP4. `tests/production/test_subtitles.py` covers the chunking/wrapping
guarantees (max 2 lines, max words/cue, non-overlapping cue timing) and `.ass` output format with no
ffmpeg dependency (pure Python). `tests/production/test_visual_relevance.py` covers the metadata
pre-filter (keyword extraction, shot-type classification, specific-match reward, generic-term
penalty, aspect bonus, shot-type-repetition penalty) and `tests/production/test_vision_validation.py`
covers the actual relevance decision (prompt content, response parsing, the hard
domain/threshold/misleading rejection rule, shortlist fall-through on rejection, the real paper-card
and grocery-shelf failures this fixes, thumbnail-only fetching) -- both with fake `AssetResult`/
`Scene`/`LlmProvider` objects, no network; `tests/production/test_asset_acquisition.py` exercises the
same scenarios end to end through `acquire_assets()`. `tests/production/test_telegram_approval.py`
covers the approval keyboard/callback_data shape, malformed/unrelated-callback handling, the full
approve/reject/regenerate/timeout decision loop against a fake `TelegramClient`, approval-state
persistence, and the GitHub workflow-dispatch call (mocked `requests.post`) -- no real Telegram or
GitHub API calls. `tests/production/test_gemini_provider.py` validates the Vision call's multimodal
`input` shape (the `user_input`-step wrapper) against the real, installed `google-genai` SDK's own
request model, the same technique already used there for `generate_script()`'s `response_format`.
Everything else follows CLAUDE.md's no-public-internet-for-unit-tests rule via mocked HTTP
(providers) or fake in-memory providers (`script_agent.py`, `asset_acquisition.py`), matching the
pattern already established in `tests/research/`. Real Gemini (text and Vision)/Pexels/Pixabay/
edge-tts/Telegram connectivity is only exercised for real inside the GitHub Actions workflow itself
-- see the top-level report for what that means was and wasn't verifiable during development.

## Possible next steps (not implemented)

- A persistent webhook receiver, if approval decisions after the ~20-minute window ever prove to
  matter in practice -- would need a new always-on hosting service outside the current $0/GitHub
  Actions architecture (see "Telegram approval gate" above), so needs explicit revisiting first.
- YouTube publishing, reading `approval_state.json` for `decision == "approve"`.
- Feeding the produced video's topic back into Research Agent's persistent state.
- Background music (needs a clearly copyright-safe source and mixing logic).
- Scheduling the production workflow once a manual run has been proven reliable.
- Analytics, affiliate automation.

Deciding and implementing any of the above is a separate, explicitly scoped task.
