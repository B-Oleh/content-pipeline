# Video Production Pipeline

First end-to-end milestone that turns one Research Agent topic into a real rendered vertical MP4
delivered to Telegram. Covers CLAUDE.md pipeline stages 4-10 (Script Agent, Visual Planner, Asset
Acquisition, Voice Generation, Video Assembly, Automated QA, Telegram — approval buttons excluded
for now, see "Telegram delivery" below). Stage 1 (Research Agent) is reused unchanged — see
[docs/RESEARCH_AGENT.md](RESEARCH_AGENT.md).

Engineering rules (secrets, dependencies, logging, testing, provider abstraction) are defined once
in CLAUDE.md and are not repeated here.

## Goal and non-goals

The goal of this milestone is narrow and concrete: **one manual GitHub Actions run produces one
real MP4 and delivers it to Telegram.** Everything here is built to reach that, and no further:

- No Telegram Approve/Regenerate/Reject buttons yet (webhooks/callbacks are a separate milestone).
- No scheduling/cron.
- No YouTube publishing, analytics, or affiliate automation.
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
  asset_acquisition.py                 Per-scene Pexels/Pixabay video (then photo) search+download
  voice_generation.py                  Per-scene edge-tts narration; scene duration = its own audio
  subtitles.py                         Groups word timings into short caption chunks; writes .srt
  video_assembly.py                    ffmpeg: scale/crop to 1080x1920, concat scenes, burn subtitles
  qa.py                                ffprobe-based deterministic checks before Telegram delivery
  telegram_delivery.py                 Builds the caption, sends the MP4
  pipeline.py                          Orchestrates all of the above, in order
  ffmpeg_utils.py                      Shared ffprobe/ffmpeg-binary helpers
  providers/
    llm.py                             LlmProvider interface + GeminiProvider
    visual.py                          VisualAssetProvider interface + PexelsProvider/PixabayProvider
    voice.py                           VoiceProvider interface + EdgeTtsProvider
    telegram_client.py                 Thin Telegram Bot API client (getMe, sendVideo)
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
asset_acquisition.acquire_assets() -> mutates each Scene with a downloaded asset_path
voice_generation.generate_narration() -> mutates each Scene with audio_path + duration_seconds;
                                          returns global SubtitleCue list
subtitles.write_srt() -> captions.srt
video_assembly.render_video() -> output/final_video.mp4 (1080x1920, H.264, AAC, burned subtitles)
qa.run_qa() -> QAResult
  if passed: telegram_delivery.deliver_video() -> Telegram
  if failed: pipeline stops, nothing is sent (see "QA" below)
```

Intermediate files (downloaded assets, per-scene audio, `captions.srt`, `script.json`,
`qa_result.json`) live under `data/production/` -- transient, gitignored, exactly like Research
Agent's own `data/research/` (see docs/RESEARCH_AGENT.md "Persistent state vs transient output").
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

**Gemini preflight.** `GeminiProvider.ping()` sends nothing beyond `model` and a fixed prompt
("Reply exactly with OK") -- no tools, no response schema -- specifically so a plain
connectivity/auth failure is never confused with a structured-output or tool-calling problem.
Both `ping()` and `generate_script()` read `interaction.output_text` (the SDK's own "concatenated
text from the last model output" convenience field); if it is empty, the failure reason comes from
the interaction's own `status` and `errors[].message` (both provider-supplied, safe to log in
full -- see `_describe_incomplete_interaction`), not a guess. A ping failure raises
`GeminiPingError`; a script-generation failure raises `ScriptGenerationError` -- two distinct
exception types, so `preflight.py` never reports a connectivity problem as if it were a script
validation problem, and vice versa. `preflight.py::_safe_error_message` always names the
underlying exception's class alongside whatever safe message is available, instead of collapsing
every Gemini failure into one indistinguishable string.

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

## Visuals

`providers/visual.py` implements `PexelsProvider` and `PixabayProvider` against their official,
documented, free-tier APIs (video search first, photo search as fallback) -- no scraping. Script
Agent is instructed to write `visual_search_queries` as generic, stock-friendly phrases (e.g.
"gaming pc setup rgb") rather than exact game/product names, since stock libraries will not have
that exact footage -- this is also why topic selection above prefers pillars where generic footage
is honestly illustrative. `asset_acquisition.py` tries every query against both providers' video
search before falling back to photo search on either; a still photo gets a mild continuous zoom
(`zoompan` in `video_assembly.py`) so no scene sits completely static.

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
shifts each scene's word timings onto the whole video's timeline, so one global `.srt` file covers
the entire runtime. `video_assembly.py` burns it in via ffmpeg's `subtitles` filter with a
`force_style` tuned for vertical mobile viewing (large font, bottom-center, `MarginV=180` to stay
clear of the bottom safe area).

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

## Telegram delivery

`providers/telegram_client.py` is a thin Bot API client: `getMe()` for preflight, `sendVideo()` for
delivery -- no callback/webhook handling. The caption (`telegram_delivery.py::build_caption()`)
includes the topic, proposed title, content role, why it was selected (Research Agent's own scoring
reasoning), the overall research score, and a note on how many scoring dimensions are backed by
real evidence vs heuristic (see docs/RESEARCH_AGENT.md "Heuristic vs real data"). Approve/Regenerate
/Reject buttons are explicitly out of scope for this milestone (see CLAUDE.md "Telegram approval
gate" -- that stage still needs webhook infrastructure this milestone does not build).

## Secrets

`GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` are
read from the environment only (`os.getenv`, GitHub Secrets in CI / `.env` locally) -- never
hard-coded, never logged. `preflight.py` reports only presence/absence and pass/fail per service,
using a provider's own safe error field when available (e.g. `google.genai.errors.APIError.message`,
which comes from Gemini's own JSON error body) or just the exception's type name otherwise -- never
a raw exception string, which for a lower-level transport error can include request details. See
"Visuals" above for the two specific URL-based leak risks (Pixabay, Telegram) and how they're
closed.

## GitHub Actions workflow

`.github/workflows/produce_video.yml` -- manually triggered only (`workflow_dispatch`; no
`schedule`/`cron`/`repository_dispatch`). Steps: checkout -> Python 3.11 -> install dependencies ->
ensure ffmpeg is installed (checks first, `apt-get install` only if missing) -> run the full
automated test suite -> preflight (`python -m scripts.production.preflight`, its own step so a
credential problem fails clearly and immediately) -> `python -m scripts.produce_video` (research
through Telegram delivery). `permissions: contents: read` -- this workflow never commits or pushes
(see "Relationship to Research Agent state" above). On failure, `data/production/`, `output/`, and
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
PATH). Everything else follows CLAUDE.md's no-public-internet-for-unit-tests rule via mocked HTTP
(providers) or fake in-memory providers (`script_agent.py`), matching the pattern already
established in `tests/research/`. Real Gemini/Pexels/Pixabay/edge-tts/Telegram connectivity is only
exercised for real inside the GitHub Actions workflow itself -- see the top-level report for what
that means was and wasn't verifiable during development.

## Possible next steps (not implemented)

- Telegram Approve/Regenerate/Reject buttons (needs webhook/callback infrastructure).
- Feeding the produced video's topic back into Research Agent's persistent state.
- Background music (needs a clearly copyright-safe source and mixing logic).
- Scheduling the production workflow once a manual run has been proven reliable.
- YouTube publishing, analytics, affiliate automation.

Deciding and implementing any of the above is a separate, explicitly scoped task.
