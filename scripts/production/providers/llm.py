"""LLM provider boundary for Script Agent.

Thin and replaceable per CLAUDE.md "Provider abstraction": script_agent.py
only ever calls the LlmProvider interface below, never the Gemini SDK
directly, so a future free-tier alternative can be swapped in by adding one
class here.
"""

from __future__ import annotations

import base64
import json
import random
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Optional, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.production.content_brief import ContentBrief

from scripts.production.models import Scene, VideoScript
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

# Gemini's free tier is small (the incident this fixes: HTTP 429 "Quota
# exceeded for generativelanguage.googleapis.com/generate_content_free_tier_requests,
# limit: 20" mid-run) -- a small, safe retry cap with exponential backoff +
# jitter, honoring the server's own Retry-After when it provides one,
# turns a single transient rate-limit hit into a short wait instead of an
# immediate pipeline failure (see _call_with_retry). This does NOT retry
# any other kind of failure (auth, malformed response, etc.) -- those fail
# immediately, unchanged.
GEMINI_MAX_RETRIES = 3
GEMINI_BASE_RETRY_DELAY_SECONDS = 2.0
GEMINI_MAX_RETRY_DELAY_SECONDS = 60.0
GEMINI_RETRY_JITTER_RATIO = 0.25

# Gemini's free tier allows ~20 generate_content requests per MINUTE
# (verify: HTTP 429 "Quota exceeded for metric
# generativelanguage.googleapis.com/generate_content_free_tier_requests,
# limit: 20"). A *per-call* retry window alone is not enough: the overnight
# batch's real failure was a burst of Script + content-brief + Vision calls
# saturating the rolling minute before any single call's retry could help.
# The GeminiRateLimiter below coordinates every Gemini call that flows
# through one provider instance so bursts are smoothed to a sustainable rate
# (~MAX/60s) BEFORE they hit the server, and a seen 429's retry window is
# respected by *all* callers, not just the one that received it.
GEMINI_RATE_LIMIT_MAX_PER_WINDOW = 20
GEMINI_RATE_LIMIT_WINDOW_SECONDS = 60.0
# Slightly under MAX/WINDOW so an exact 20/60s burst never sits right on
# the limit: 3.2s spacing sustains ~18.75 requests/min, leaving ~1.25
# requests/min of headroom inside the free-tier cap.
GEMINI_RATE_LIMIT_MIN_INTERVAL_SECONDS = 3.2
GEMINI_RATE_LIMIT_SATURATION_MARGIN_SECONDS = 2.0
GEMINI_RATE_LIMIT_MAX_SINGLE_WAIT_SECONDS = 90.0

_T = TypeVar("_T")

# The most concretely fabricable "hard numbers" this niche's rules forbid
# inventing (see CLAUDE.md "Fact checking" and "Research and opportunity
# scoring rules"). This is a safety net on top of the prompt instructions
# below, not a substitute for them -- it deliberately does not try to catch
# every possible fabrication (e.g. a bare year), since a niche where topics
# themselves legitimately mention years/models would make that guard too
# noisy to trust. FPS/price/percentage-performance numbers are the sharpest,
# least ambiguous signal of an invented benchmark or price.
_FABRICATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d{1,4}\s?fps\b", re.IGNORECASE), "a specific FPS number"),
    (re.compile(r"[$€£]\s?\d+(\.\d{1,2})?"), "a specific price"),
    (
        re.compile(r"\b\d{1,3}\s?%\s*(faster|slower|better|worse|improvement|increase|decrease|boost)\b", re.IGNORECASE),
        "a specific performance percentage",
    ),
]


class ScriptGenerationError(RuntimeError):
    """Raised when Script Agent cannot produce a script that respects the
    "never invent FPS/prices/specs/dates/popularity/performance" rule, when
    a structured-script Gemini call is unusable, or when its response is
    malformed/empty."""


class GeminiPingError(RuntimeError):
    """Raised when the minimal preflight connectivity check (GeminiProvider.ping)
    fails -- kept distinct from ScriptGenerationError so a preflight
    connectivity/auth problem is never reported as if it were a structured
    script generation problem (see preflight.py)."""


class VisionEvaluationError(RuntimeError):
    """Raised when a Gemini Vision call for candidate-asset relevance fails
    or returns no usable output (see vision_validation.py, which catches
    this per-candidate so one failed evaluation cannot abort a whole
    scene's asset search)."""


def find_fabrication_risks(text: str) -> list[str]:
    """Return a list of human-readable violations found in text, if any."""
    violations = []
    for pattern, description in _FABRICATION_PATTERNS:
        if pattern.search(text):
            violations.append(f"{description} ({pattern.pattern!r} matched)")
    return violations


class LlmProvider:
    """Interface every LLM provider must implement."""

    def generate_content_brief(self, prompt: str) -> str:
        """Return the raw JSON response for a content brief."""
        raise NotImplementedError

    def generate_script(self, prompt: str) -> str:
        """Return the raw text response for a fully-built prompt."""
        raise NotImplementedError

    def evaluate_visual_candidate(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        """Return the raw JSON text response for a vision-based relevance
        evaluation of one candidate image (see vision_validation.py, which
        owns the prompt content and response parsing -- this method is only
        the thin vendor-call boundary, per CLAUDE.md "Provider abstraction").
        """
        raise NotImplementedError


# JSON schema for structured script output via the Interactions API's
# response_format (see GeminiProvider.generate_script). This is enforced
# server-side on top of -- not instead of -- _SCRIPT_JSON_INSTRUCTIONS
# below, which also carries semantic rules (scene count, word count,
# generic visual queries, no fabricated claims) a JSON schema cannot
# express.
SCRIPT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hook": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration_line": {"type": "string"},
                    "on_screen_text": {"type": "string"},
                    "visual_search_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 5,
                    },
                },
                "required": ["narration_line"],
            },
        },
        "evidence_references": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "hook", "scenes"],
}

# JSON schema for the Gemini Vision candidate-relevance call (see
# vision_validation.py, which owns the actual prompt text and hard
# rejection rules -- this schema only shapes the response).
VISION_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "computer_domain": {"type": "boolean"},
        "scene_relevance_score": {"type": "integer"},
        "misleading": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["computer_domain", "scene_relevance_score", "misleading", "reason"],
}

_PING_PROMPT = "Reply exactly with OK"

# Statuses the Interactions API can return without ever raising a Python
# exception (see google.genai.interactions.InteractionStatus) -- anything
# other than "completed" means there is no usable output_text, and the
# reason should come from the interaction's own `errors`, not a guess.
_INTERACTION_FAILURE_HINT = "did not complete successfully"


# Matches Gemini's "Please retry in 24.354420322s." phrasing (decimal
# seconds, always followed by "s" then end-of-sentence) -- the sanitized,
# last-resort fallback signal for both _is_rate_limited() and
# _extract_retry_after_seconds() when no structured field carries the same
# information. See their docstrings for why this is needed at all: the
# real, installed google-genai SDK (2.22.0) raises
# google.genai._gaos.lib.compat_errors.RateLimitError for a 429, which has
# neither a `.code` attribute nor Google's older RetryInfo detail shape --
# the retry delay only exists as this sentence inside the error's own
# message text.
_RETRY_IN_SECONDS_PATTERN = re.compile(r"retry in\s+([0-9]+(?:\.[0-9]+)?)\s*s\b", re.IGNORECASE)


def _find_nested_error_dict(payload: Any) -> Optional[dict]:
    """Returns the innermost `{"code": ..., "message": ..., "status": ...}`
    error dict out of a parsed Gemini error body, whether it arrived as
    `{"error": {...}}` (the raw JSON shape) or already unwrapped."""
    if not isinstance(payload, dict):
        return None
    inner = payload.get("error", payload)
    return inner if isinstance(inner, dict) else None


def _extract_message_text(exc: Exception) -> str:
    """The most specific human-readable error text available: the nested
    `body["error"]["message"]` Gemini itself wrote (if the SDK parsed a
    structured body), else the exception's own `.message`, else `str(exc)`.
    Used only for the sanitized last-resort fallback checks below -- never
    logged in full, only searched for a known-safe substring/pattern.
    """
    error_dict = _find_nested_error_dict(getattr(exc, "body", None)) or _find_nested_error_dict(getattr(exc, "details", None))
    if error_dict:
        message = error_dict.get("message")
        if isinstance(message, str) and message:
            return message
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        return message
    return str(exc)


def _is_rate_limited(exc: Exception) -> bool:
    """True for Gemini's HTTP 429 rate-limit error.

    A real GitHub Actions run proved the original `getattr(exc, "code",
    None) == 429` check silently never matches: the installed google-genai
    SDK (2.22.0) raises `google.genai._gaos.lib.compat_errors.RateLimitError`
    for a 429, which has a `status_code` attribute (inherited from
    `APIStatusError`), NOT a `code` attribute at all. Checked in priority
    order, most specific/reliable first, so this keeps working even if a
    future SDK version changes which of these happens to be populated:

    1. The real SDK's own `RateLimitError` type, if importable (import is
       lazy and best-effort -- this module must stay importable even if
       that internal SDK path ever moves).
    2. `status_code == 429` (the current SDK's `APIStatusError` shape).
    3. `code == 429` (the older `google.genai.errors.ClientError` shape --
       kept for compatibility, not because it's what's installed now).
    4. A nested `response.status_code` or a parsed error body/detail whose
       own `code` field is 429.
    5. Sanitized fallback: "Error code: 429" or "too_many_requests"
       (case-insensitive) in the error's own message text -- only reached
       when none of the structured signals above matched.
    """
    try:
        from google.genai._gaos.lib.compat_errors import RateLimitError as _RealRateLimitError
    except ImportError:
        _RealRateLimitError = None
    if _RealRateLimitError is not None and isinstance(exc, _RealRateLimitError):
        return True

    if getattr(exc, "status_code", None) == 429:
        return True

    if getattr(exc, "code", None) == 429:
        return True

    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    for attr_name in ("body", "details"):
        error_dict = _find_nested_error_dict(getattr(exc, attr_name, None))
        if error_dict is not None and error_dict.get("code") == 429:
            return True

    text = _extract_message_text(exc).lower()
    return "error code: 429" in text or "too_many_requests" in text


def _extract_retry_after_seconds(exc: Exception) -> Optional[float]:
    """Best-effort read of a server-provided retry delay, in priority order:

    1. The standard HTTP `Retry-After` response header, if the SDK
       attached a real response object.
    2. Google's structured `RetryInfo` error detail
       (`{"retryDelay": "42s"}`), wherever this SDK version happens to
       expose the parsed error body (`.details` on the older
       `google.genai.errors.ClientError`, `.body` on the current
       `google.genai._gaos.lib.compat_errors.RateLimitError`).
    3. Sanitized fallback: Gemini's own "Please retry in 24.354420322s."
       phrasing, parsed directly out of the error message text (supports
       decimal seconds) -- only reached when neither structured signal
       above was present/parseable.

    Returns None if nothing above is available, so the caller falls back
    to its own exponential backoff instead of guessing.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            raw = headers.get("Retry-After") or headers.get("retry-after")
        except AttributeError:
            raw = None
        if raw:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass

    for attr_name in ("details", "body"):
        error_dict = _find_nested_error_dict(getattr(exc, attr_name, None))
        if error_dict is None:
            continue
        for item in error_dict.get("details") or []:
            if not isinstance(item, dict):
                continue
            retry_delay = item.get("retryDelay")
            if isinstance(retry_delay, str) and retry_delay.endswith("s"):
                try:
                    return float(retry_delay[:-1])
                except ValueError:
                    continue

    match = _RETRY_IN_SECONDS_PATTERN.search(_extract_message_text(exc))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def _call_with_retry(
    operation: Callable[[], _T],
    *,
    max_retries: int = GEMINI_MAX_RETRIES,
    rate_limiter: "Optional[GeminiRateLimiter]" = None,
) -> _T:
    """Runs `operation()`, retrying only on HTTP 429 (see _is_rate_limited)
    up to `max_retries` times with exponential backoff + jitter -- or the
    server's own Retry-After/RetryInfo delay when it provides one. Any
    other exception (auth failure, malformed response, a non-429 HTTP
    error, ...) propagates immediately, unretried.

    `rate_limiter` (if given) paces the request *before* the first attempt
    and publishes the server-requested retry delay back into the shared
    limiter when a 429 IS received, so a rate-limit no longer only delays
    the one caller that happened to get it -- every subsequent Gemini call
    on the same provider waits for the window to clear (see
    GeminiRateLimiter and "Gemini quota coordination" in
    docs/PRODUCTION_PIPELINE.md). The retry loop deliberately does NOT
    re-consult the limiter between attempts: its own sleep already honors
    the saturation window, so requerying would double-sleep.
    """
    attempts_made = 0
    needs_wait = True
    while True:
        if needs_wait and rate_limiter is not None:
            rate_limiter.wait_until_allowed()
        needs_wait = True
        try:
            result = operation()
            if rate_limiter is not None:
                rate_limiter.record_success()
            return result
        except Exception as exc:  # noqa: BLE001 -- only 429s are handled here; everything else re-raises below
            if not _is_rate_limited(exc) or attempts_made >= max_retries:
                raise
            attempts_made += 1
            delay = _extract_retry_after_seconds(exc)
            if delay is None:
                delay = min(GEMINI_MAX_RETRY_DELAY_SECONDS, GEMINI_BASE_RETRY_DELAY_SECONDS * (2 ** (attempts_made - 1)))
            else:
                delay = min(delay, GEMINI_MAX_RETRY_DELAY_SECONDS)
            delay += random.uniform(0, delay * GEMINI_RETRY_JITTER_RATIO)
            if rate_limiter is not None:
                # Tell the whole shared limiter the window is saturated for
                # at least this long, so the NEXT Gemini-heavy call (e.g. the
                # next scene's Vision check, or the next candidate's content
                # brief) pauses too instead of piling into a still-hot window.
                rate_limiter.record_rate_limit(delay)
            logger.warning("Gemini rate limited, retrying in %.1f seconds (attempt %d/%d)", delay, attempts_made, max_retries)
            time.sleep(delay)
            # The sleep above already paced the gap to the retry; the next
            # top-level operation's wait_until_allowed() will see the recorded
            # saturation and pause before firing again.
            needs_wait = False


class GeminiRateLimiter:
    """Coordinates the pacing of every Gemini generate-content call made
    through one GeminiProvider instance.

    Why this exists: the failed overnight-batch run (see
    docs/BATCH_MODE.md) hit HTTP 429 "Quota exceeded ...
    generate_content_free_tier_requests, limit: 20" not because one call
    misbehaved but because Script + content-brief + per-scene Vision calls
    were fired back-to-back, saturating Gemini's free-tier 20-requests-per-
    minute window mid-candidate. Per-call retries alone cannot fix that: by
    the time one call retries, the window is still hot, so the retry and
    every other call just pile on again.

    Callers use wait_until_allowed() before each request and record_success()
    / record_rate_limit(delay) after it. A fresh limiter paces at most one
    request every MIN_INTERVAL seconds (≈19/min, just under the 20/min cap)
    and never allows more than MAX_REQUESTS_PER_WINDOW successful requests
    in any rolling WINDOW_SECONDS. When a 429 is seen, record_rate_limit
    stores a saturation deadline that wait_until_allowed() enforces for ALL
    callers (not just the one that got the 429), so the next Gemini-heavy
    stage does not start while the rate-limit window is still draining.

    Single-threaded pipeline; the lock exists so a future concurrent
    orchestration cannot double-fire. All sleeps go through this module's
    `time.sleep`, so tests can monkeypatch
    `scripts.production.providers.llm.time.sleep` to keep them instant.
    """

    def __init__(
        self,
        *,
        max_requests_per_window: int = GEMINI_RATE_LIMIT_MAX_PER_WINDOW,
        window_seconds: float = GEMINI_RATE_LIMIT_WINDOW_SECONDS,
        min_interval_seconds: float = GEMINI_RATE_LIMIT_MIN_INTERVAL_SECONDS,
        saturation_margin_seconds: float = GEMINI_RATE_LIMIT_SATURATION_MARGIN_SECONDS,
        max_single_wait_seconds: float = GEMINI_RATE_LIMIT_MAX_SINGLE_WAIT_SECONDS,
    ) -> None:
        self._max_per_window = max_requests_per_window
        self._window = window_seconds
        self._min_interval = min_interval_seconds
        self._saturation_margin = saturation_margin_seconds
        self._max_single_wait = max_single_wait_seconds
        self._request_times: deque[float] = deque()
        self._saturation_until = 0.0
        self._lock = threading.Lock()

    def wait_until_allowed(self) -> None:
        """Sleep (bounded) until this request may fire without making the
        free-tier window worse: the seen-429 saturation deadline, the rolling
        window capacity, and the minimum interval after the last success are
        all honored, whichever is the longest."""
        with self._lock:
            now = time.monotonic()
            wait = 0.0

            if self._saturation_until > now:
                wait = max(wait, self._saturation_until - now + self._saturation_margin)

            while self._request_times and now - self._request_times[0] >= self._window:
                self._request_times.popleft()

            if len(self._request_times) >= self._max_per_window:
                wait = max(wait, self._request_times[0] + self._window - now + self._saturation_margin)

            if self._request_times and now - self._request_times[-1] < self._min_interval:
                wait = max(wait, self._min_interval - (now - self._request_times[-1]))

            if wait > 0:
                time.sleep(min(wait, self._max_single_wait))

    def record_success(self) -> None:
        """Record that one generate-content request was accepted by the
        server (counts toward the free-tier quota and resets the pacing
        interval watermark)."""
        with self._lock:
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= self._window:
                self._request_times.popleft()
            self._request_times.append(now)
            # Keep the deque bounded even if callers stop waiting between
            # requests for some reason -- stale timestamps are only needed for
            # the rolling window anyway.
            while len(self._request_times) > self._max_per_window * 2:
                self._request_times.popleft()

    def record_rate_limit(self, retry_after_seconds: Optional[float]) -> None:
        """Publish a rate-limit hit to every caller: the server asked us to
        wait at least `retry_after_seconds` before the next request (or, when
        absent, our own backoff estimate), so wait_until_allowed() will now
        pause until that deadline clears -- not just for this call, but for
        the next call anywhere on this provider."""
        if retry_after_seconds is None:
            return
        with self._lock:
            self._saturation_until = max(self._saturation_until, time.monotonic() + retry_after_seconds)


def _describe_incomplete_interaction(interaction: Any) -> str:
    """A safe, useful description of why an Interaction has no output_text.

    Uses only the interaction's own status and error messages (both
    provider-supplied, never request/credential data) -- see
    preflight.py's "never print secret values" rule.
    """
    status = getattr(interaction, "status", "unknown")
    errors = getattr(interaction, "errors", None) or []
    messages = [error.message for error in errors if getattr(error, "message", None)]
    if messages:
        return f"Gemini interaction {_INTERACTION_FAILURE_HINT} (status={status}): {'; '.join(messages)}"
    return f"Gemini interaction {_INTERACTION_FAILURE_HINT} (status={status}) with no output text"


class GeminiProvider(LlmProvider):
    """Google Gemini, via the official google-genai SDK's Interactions API
    (client.interactions.create) -- not the older Models.generate_content,
    whose automatic function calling (AFC) machinery was firing even for a
    plain text-only prompt with no tools configured (see
    docs/PRODUCTION_PIPELINE.md "Gemini preflight" for the incident this
    fixed). Interactions has no client-side AFC concept at all: tools are
    explicit server-side declarations (google.genai.interactions.Tool) that
    this provider never passes.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        client: Any = None,
        rate_limiter: "Optional[GeminiRateLimiter]" = None,
    ) -> None:
        if client is None:
            # Imported lazily so importing this module (e.g. for the
            # fabrication guard alone, in tests) never requires the
            # google-genai package to be configured with a real key.
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self._model = model
        # One limiter per provider instance so ALL of its Gemini calls
        # (content brief, Script Agent, and per-scene Vision) coordinate
        # against the same free-tier minute -- see GeminiRateLimiter.
        # Injectable so tests can substitute a tuned/no-op limiter.
        self._rate_limiter = rate_limiter if rate_limiter is not None else GeminiRateLimiter()

    def generate_script(self, prompt: str) -> str:
        interaction = _call_with_retry(
            lambda: self._client.interactions.create(
                model=self._model,
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": SCRIPT_JSON_SCHEMA,
                },
            ),
            rate_limiter=self._rate_limiter,
        )
        text = getattr(interaction, "output_text", None)
        if not text:
            raise ScriptGenerationError(_describe_incomplete_interaction(interaction))
        return text

    def generate_content_brief(self, prompt: str) -> str:
        interaction = _call_with_retry(
            lambda: self._client.interactions.create(
                model=self._model,
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": CONTENT_BRIEF_SCHEMA,
                },
            ),
            rate_limiter=self._rate_limiter,
        )
        text = getattr(interaction, "output_text", None)
        if not text:
            raise ScriptGenerationError(_describe_incomplete_interaction(interaction))
        return text

    def evaluate_visual_candidate(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        """One multimodal (text + image) Interactions call -- confirmed
        against the real, installed google-genai SDK's own request model
        that `input` must be a list of `{"type": "user_input", "content":
        [...]}` steps (a bare list of content dicts is silently
        misinterpreted as unrecognized "steps", not content -- see
        tests/production/test_gemini_provider.py for the same real-SDK
        validation approach used for generate_script's response_format).
        """
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        interaction = _call_with_retry(
            lambda: self._client.interactions.create(
                model=self._model,
                input=[
                    {
                        "type": "user_input",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image", "data": image_b64, "mime_type": mime_type},
                        ],
                    }
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": VISION_EVALUATION_SCHEMA,
                },
            ),
            rate_limiter=self._rate_limiter,
        )
        text = getattr(interaction, "output_text", None)
        if not text:
            raise VisionEvaluationError(_describe_incomplete_interaction(interaction))
        return text

    def ping(self) -> None:
        """Minimal, tools-free, schema-free text-only connectivity check
        used by preflight.py.

        Deliberately passes nothing beyond model + input: no tools, no
        automatic function calling, no response_format/JSON schema -- so a
        plain connectivity/auth failure can never be confused with (or
        masked by) a structured-output or tool-calling problem.
        """
        interaction = _call_with_retry(
            lambda: self._client.interactions.create(model=self._model, input=_PING_PROMPT),
            rate_limiter=self._rate_limiter,
        )
        text = getattr(interaction, "output_text", None)
        if not text:
            raise GeminiPingError(_describe_incomplete_interaction(interaction))


_SCRIPT_JSON_INSTRUCTIONS = """
Respond with ONLY a single JSON object (no markdown fences, no commentary) with this exact shape:

{{
  "title": "short YouTube Shorts title, under 100 characters",
  "description": "1-2 sentence video description",
  "hook": "one short opening sentence that earns attention in the first 2 seconds",
  "scenes": [
    {{
      "narration_line": "1-2 short spoken sentences for this scene",
      "on_screen_text": "very short on-screen caption text, or empty string",
      "visual_search_queries": ["3 to 5 concrete, specific stock-footage search phrases for THIS scene"]
    }}
  ],
  "evidence_references": ["short phrase naming which piece of evidence supports a claim, or general phrase if none needed"]
}}

Rules:
- Produce 4 to 6 scenes.
- Total spoken narration (hook + every scene's narration_line combined) must read aloud in
  approximately 30-45 seconds at a natural pace (roughly 90-115 words total).
- Each scene's visual_search_queries must contain 3 to 5 CONCRETE, specific phrases describing what
  should visibly be on screen for THAT scene -- specific enough that a stock-video search would return
  a genuinely matching clip. Bad (too vague): "gaming", "computer". Better: "gaming laptop keyboard
  close up", "desktop gpu inside pc case", "pc game performance settings menu", "person comparing
  computer hardware", "high refresh rate gaming monitor". Vary what each query describes across the
  list for one scene (e.g. a close-up, a hardware-detail shot, a monitor/UI shot, a person/use-case
  shot) so the pipeline has real options to choose from, not five near-duplicate phrasings of the same
  shot. Also vary the dominant shot type ACROSS scenes -- avoid every scene being the same kind of shot
  (e.g. all close-ups, or all person-at-desk shots).
- Never write a visual_search_queries phrase that names a specific game title or exact product/model
  name -- stock video libraries will not have that exact footage. Do not write narration or
  on_screen_text that claims the video is showing a specific named product or game if only generic
  footage will illustrate it -- keep such lines honest about being illustrative/generic.
- NEVER state a specific FPS number, benchmark result, price, exact hardware specification, release
  date, popularity/sales statistic, or performance percentage unless it is explicitly present in the
  evidence provided below. If the evidence does not support a specific number, speak in general,
  qualitative terms instead (e.g. "smoother performance" instead of "40% faster", "check current
  pricing" instead of a dollar amount).
- Keep every sentence short and easy to follow when read aloud quickly.
- Every scene must describe something that can visibly be shown on screen. If a fact or idea is too
  abstract or conceptual to picture directly (e.g. "market trends are shifting" or "prices vary a
  lot depending on many factors"), do NOT write one long abstract scene for it -- instead split it
  into a shorter scene (or reframe it around a concrete, visible action or object: a person looking
  at a screen, a close-up of a component, a settings menu, a desk setup) so visual_search_queries can
  describe something a stock clip could actually show. Never invent a new fact or change the meaning
  of a claim to make it more visual -- only reshape HOW an already-true idea is delivered and shot.
"""


def build_script_prompt(
    *,
    title: str,
    content_pillar: str,
    content_role: str,
    monetization_path: str | None,
    summary: str | None,
    hardware_tier: str | None,
    game_title: str | None,
    scoring_reasoning: list[str],
    evidence: list[dict[str, Any]],
    content_brief: ContentBrief | None = None,
) -> str:
    """Build the full prompt text for GeminiProvider.generate_script().

    Kept as a pure function (no SDK dependency) so it is directly unit
    testable without a real API key.
    """
    evidence_lines = []
    for item in evidence:
        source = item.get("source_name", "unknown source")
        item_title = item.get("title") or ""
        item_summary = item.get("summary") or ""
        url = item.get("source_url") or ""
        evidence_lines.append(f"- [{source}] {item_title} -- {item_summary} ({url})".strip())
    evidence_block = "\n".join(evidence_lines) if evidence_lines else "(no external evidence items -- rely only on the topic itself, stay general)"

    context_lines = [
        f"Topic: {title}",
        f"Content pillar: {content_pillar}",
        f"Content role: {content_role}",
    ]
    if content_brief is not None:
        context_lines.extend([
            "Content brief (design to it):",
            f"- Target audience: {content_brief.target_audience}",
            f"- Viewer pain/problem: {content_brief.viewer_pain}",
            f"- Topic angle: {content_brief.topic_angle}",
            f"- Hook candidates: {content_brief.hook_candidates}",
            f"- Selected opening hook (YOU MUST use this verbatim as your ``hook`` field): {content_brief.selected_hook}",
            f"- Why this hook: {content_brief.hook_rationale}",
        ])
    if monetization_path:
        context_lines.append(f"Monetization angle: {monetization_path}")
    if summary:
        context_lines.append(f"Research summary: {summary}")
    if hardware_tier:
        context_lines.append(f"Hardware tier: {hardware_tier}")
    if game_title:
        context_lines.append(f"Game mentioned: {game_title}")
    if scoring_reasoning:
        context_lines.append("Why this topic was selected: " + "; ".join(scoring_reasoning))

    return (
        "You are writing a factual, honest short-form (vertical, YouTube Shorts style) video script "
        "for a gaming hardware/technology channel.\n\n"
        + "\n".join(context_lines)
        + "\n\nEvidence available for this topic:\n"
        + evidence_block
        + "\n\n"
        + _SCRIPT_JSON_INSTRUCTIONS.format()
        + (
            "\nYour `hook` field MUST be the selected opening hook from the content brief, verbatim. "
            "If the selected hook is not already honest to the evidence, use it anyway as the "
            "opening line (it is an attention device; the fabrication guard separately checks "
            "the narration for invented numbers)."
            if content_brief is not None else ""
        )
    )


def parse_script_response(raw_text: str, *, retry_note: str = "") -> dict[str, Any]:
    """Parse and lightly validate the JSON shape Gemini was asked for.

    Strips a markdown code fence if the model added one anyway. Raises
    ScriptGenerationError on invalid JSON or a missing required field --
    fail loudly rather than silently producing a broken script (see
    CLAUDE.md "Script organization").
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScriptGenerationError(f"Gemini did not return valid JSON{retry_note}: {exc}") from exc

    required = ("title", "description", "hook", "scenes")
    missing = [field for field in required if field not in data]
    if missing:
        raise ScriptGenerationError(f"Gemini response is missing required field(s): {missing}")
    if not isinstance(data["scenes"], list) or not data["scenes"]:
        raise ScriptGenerationError("Gemini response has no scenes")
    return data


def to_video_script(
    data: dict[str, Any],
    *,
    topic: str,
    content_role: str,
    candidate_id: str | None,
    overall_score: float | None,
) -> VideoScript:
    hook = str(data["hook"]).strip()
    scenes = []
    for index, raw_scene in enumerate(data["scenes"]):
        narration_line = str(raw_scene.get("narration_line", "")).strip()
        scenes.append(
            Scene(
                index=index,
                # The hook is spoken over scene 0's visual rather than as a
                # separate scene -- it is still exposed on VideoScript.hook
                # (e.g. for the Telegram caption) unmerged.
                narration_line=f"{hook} {narration_line}".strip() if index == 0 else narration_line,
                on_screen_text=(str(raw_scene["on_screen_text"]).strip() or None)
                if raw_scene.get("on_screen_text")
                else None,
                visual_search_queries=[str(q).strip() for q in raw_scene.get("visual_search_queries", []) if str(q).strip()],
            )
        )
    narration = " ".join([hook] + [str(s.get("narration_line", "")).strip() for s in data["scenes"]]).strip()
    return VideoScript(
        topic=topic,
        content_role=content_role,
        hook=hook,
        narration=narration,
        scenes=scenes,
        title=str(data["title"]).strip(),
        description=str(data["description"]).strip(),
        evidence_references=[str(r) for r in data.get("evidence_references", [])],
        candidate_id=candidate_id,
        overall_score=overall_score,
    )


CONTENT_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "target_audience": {"type": "string"},
        "viewer_pain": {"type": "string"},
        "topic_angle": {"type": "string"},
        "hook_candidates": {
            "type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3,
        },
        "selected_hook": {"type": "string"},
        "hook_rationale": {"type": "string"},
    },
    "required": ["target_audience", "viewer_pain", "topic_angle", "hook_candidates",
                 "selected_hook", "hook_rationale"],
}


def build_content_brief_prompt(
    *, title: str, content_pillar: str, content_role: str,
    monetization_path: str | None, summary: str | None, hardware_tier: str | None,
    game_title: str | None, scoring_reasoning: list[str], evidence: list[dict[str, Any]],
) -> str:
    """Build an evidence-grounded brief prompt without making provider calls."""
    context = {
        "Topic": title, "Content pillar": content_pillar, "Content role": content_role,
        "Monetization angle": monetization_path, "Research summary": summary,
        "Hardware tier": hardware_tier, "Game mentioned": game_title,
        "Scoring reasoning": scoring_reasoning, "Evidence": evidence,
    }
    return (
        "Return a concise YouTube-Shorts content brief for this exact topic in the "
        "PC-gaming/hardware niche. Define the target audience, the viewer pain/problem "
        "this video solves, and a fresh, non-generic topic angle. Return 2-3 hook candidates, "
        "each a single short opening sentence that earns attention in the first 2 seconds. "
        "selected_hook must be one of hook_candidates. Explain why that hook should retain "
        "attention in hook_rationale. Hooks may be punchy but must NEVER fabricate a "
        "number/price/spec not present in the evidence. Return only JSON matching this schema:\n"
        + json.dumps(CONTENT_BRIEF_SCHEMA, ensure_ascii=False)
        + "\n\n" + json.dumps(context, ensure_ascii=False)
    )


def parse_content_brief_response(raw_text: str, *, retry_note: str = "") -> dict[str, Any]:
    """Decode the brief JSON, accepting an optional markdown fence."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScriptGenerationError(f"Gemini did not return valid brief JSON{retry_note}: {exc}") from exc
    if not isinstance(data, dict):
        raise ScriptGenerationError(f"Gemini brief must be an object{retry_note}")
    missing = [field for field in CONTENT_BRIEF_SCHEMA["required"] if field not in data]
    if missing:
        raise ScriptGenerationError(f"Gemini brief is missing required field(s){retry_note}: {missing}")
    return data


def to_content_brief(data: dict[str, Any], *, title: str) -> ContentBrief:
    """Validate and normalize a decoded brief; reject malformed hook selections."""
    from scripts.production.content_brief import ContentBrief

    if not isinstance(data, dict):
        raise ScriptGenerationError(f"Invalid content brief for {title!r}: expected object")
    values = {}
    for field in CONTENT_BRIEF_SCHEMA["required"]:
        value = data.get(field)
        if field == "hook_candidates":
            if not isinstance(value, list) or any(not isinstance(h, str) or not h.strip() for h in value):
                raise ScriptGenerationError(f"Invalid hook_candidates for {title!r}")
            value = [hook.strip() for hook in value[:3]]
            if len(value) < 2:
                raise ScriptGenerationError(f"Content brief for {title!r} needs at least 2 hooks")
        else:
            if not isinstance(value, str) or not value.strip():
                raise ScriptGenerationError(f"Invalid brief field {field!r} for {title!r}")
            value = str(value).strip()
        values[field] = value
    if values["selected_hook"].casefold() not in {hook.casefold() for hook in values["hook_candidates"]}:
        raise ScriptGenerationError(f"selected_hook is not among hook_candidates for {title!r}")
    return ContentBrief(**values)
