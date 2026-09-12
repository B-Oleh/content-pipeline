"""GeminiProvider tests using a fake injected client -- no network, no real
API key (see GeminiProvider(client=...) dependency injection).

Covers the incident this module fixed: Models.generate_content's automatic
function calling (AFC) firing even for a plain text-only prompt with no
tools configured, which produced an empty response and masked the real
"Gemini is unreachable/misconfigured" signal as a generic
ScriptGenerationError. The Interactions API (client.interactions.create)
has no client-side AFC concept, so these tests assert the exact call shape
sent -- proving no tools/AFC/schema leak into the minimal ping.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from scripts.production.providers.llm import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_MAX_RETRIES,
    GeminiPingError,
    GeminiProvider,
    ScriptGenerationError,
    VisionEvaluationError,
    _extract_retry_after_seconds,
    _is_rate_limited,
)

try:
    from google.genai._gaos.lib.compat_errors import RateLimitError as _RealRateLimitError
except ImportError:  # pragma: no cover -- exercised only when the real SDK is installed
    _RealRateLimitError = None

requires_real_sdk_rate_limit_error = pytest.mark.skipif(
    _RealRateLimitError is None, reason="google.genai._gaos.lib.compat_errors.RateLimitError is not importable in this environment"
)


class _FakeInteractions:
    def __init__(self, output_text="OK", status="completed", errors=None):
        self.calls: list[dict] = []
        self._output_text = output_text
        self._status = status
        self._errors = errors or []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self._output_text, status=self._status, errors=self._errors)


def _provider(interactions: _FakeInteractions) -> GeminiProvider:
    fake_client = SimpleNamespace(interactions=interactions)
    return GeminiProvider(api_key="unused-with-fake-client", client=fake_client)


def test_ping_calls_interactions_create_with_only_model_and_input():
    interactions = _FakeInteractions(output_text="OK")
    provider = _provider(interactions)

    provider.ping()

    assert len(interactions.calls) == 1
    call = interactions.calls[0]
    assert call == {"model": DEFAULT_GEMINI_MODEL, "input": "Reply exactly with OK"}


def test_ping_never_passes_tools_or_afc_or_response_format():
    """The exact regression this fixes: no tools/automatic_function_calling/
    response_format key may ever appear in a ping() call."""
    interactions = _FakeInteractions(output_text="OK")
    provider = _provider(interactions)

    provider.ping()

    call = interactions.calls[0]
    for forbidden_key in ("tools", "tool_config", "automatic_function_calling", "response_format", "response_mime_type"):
        assert forbidden_key not in call


def test_ping_raises_gemini_ping_error_not_script_generation_error_on_empty_response():
    interactions = _FakeInteractions(output_text=None, status="completed")
    provider = _provider(interactions)

    with pytest.raises(GeminiPingError):
        provider.ping()


def test_ping_error_message_includes_status_and_provider_error_text():
    interactions = _FakeInteractions(
        output_text=None,
        status="failed",
        errors=[SimpleNamespace(message="model overloaded, try again")],
    )
    provider = _provider(interactions)

    with pytest.raises(GeminiPingError) as exc_info:
        provider.ping()

    message = str(exc_info.value)
    assert "failed" in message
    assert "model overloaded, try again" in message


def test_generate_script_sends_response_format_with_json_schema_and_no_tools():
    interactions = _FakeInteractions(output_text='{"title": "T"}')
    provider = _provider(interactions)

    provider.generate_script("some prompt")

    call = interactions.calls[0]
    assert call["model"] == DEFAULT_GEMINI_MODEL
    assert call["input"] == "some prompt"
    assert call["response_format"]["type"] == "text"
    assert call["response_format"]["mime_type"] == "application/json"
    assert "schema" in call["response_format"]
    assert "schema_" not in call["response_format"]
    assert "tools" not in call
    assert "automatic_function_calling" not in call


def test_generate_script_response_format_validates_against_the_real_sdk_request_model():
    """A fake client's create(**kwargs) accepts any kwargs, so the tests
    above alone cannot catch a wrong dict key (e.g. "schema_" vs "schema" --
    see the incident this test guards against). This instead feeds the
    exact call GeminiProvider makes into google.genai's own real,
    installed request-validation model, proving the real SDK accepts it and
    resolves it to the specific typed response-format variant rather than
    silently falling back to an untyped dict.
    """
    from google.genai.interactions import CreateModelInteraction, TextResponseFormat

    interactions = _FakeInteractions(output_text='{"title": "T"}')
    provider = _provider(interactions)

    provider.generate_script("some prompt")

    call = interactions.calls[0]

    # Validate against the real SDK's request model -- raises if the shape
    # (including the response_format key names) is not actually accepted.
    validated = CreateModelInteraction.model_validate(call)

    assert isinstance(validated.response_format, TextResponseFormat)
    assert validated.response_format.schema_ == call["response_format"]["schema"]

    # And confirm what actually gets sent over the wire uses "schema" (the
    # key the current official Interactions API documentation specifies),
    # regardless of which key the SDK happened to accept as input.
    wire_body = validated.model_dump(by_alias=True, exclude_none=True)
    assert wire_body["response_format"]["schema"] == call["response_format"]["schema"]
    assert "schema_" not in wire_body["response_format"]


def test_generate_script_raises_script_generation_error_not_ping_error_on_empty_response():
    interactions = _FakeInteractions(output_text="")
    provider = _provider(interactions)

    with pytest.raises(ScriptGenerationError):
        provider.generate_script("prompt")


def test_generate_script_returns_output_text_on_success():
    interactions = _FakeInteractions(output_text='{"title": "Hello"}')
    provider = _provider(interactions)

    result = provider.generate_script("prompt")

    assert result == '{"title": "Hello"}'


def test_evaluate_visual_candidate_sends_multimodal_input_and_returns_output_text():
    interactions = _FakeInteractions(output_text='{"computer_domain": true}')
    provider = _provider(interactions)

    result = provider.evaluate_visual_candidate(b"\xff\xd8fakejpeg", "image/jpeg", "Evaluate this image.")

    assert result == '{"computer_domain": true}'
    call = interactions.calls[0]
    assert call["model"] == DEFAULT_GEMINI_MODEL
    assert call["response_format"]["type"] == "text"
    assert "schema" in call["response_format"]
    step = call["input"][0]
    assert step["type"] == "user_input"
    assert step["content"][0] == {"type": "text", "text": "Evaluate this image."}
    assert step["content"][1]["type"] == "image"
    assert step["content"][1]["mime_type"] == "image/jpeg"
    # base64-encoded, not raw bytes, on the wire.
    import base64

    assert step["content"][1]["data"] == base64.b64encode(b"\xff\xd8fakejpeg").decode("ascii")


def test_evaluate_visual_candidate_request_validates_against_the_real_sdk_request_model():
    """Same real-SDK validation approach as
    test_generate_script_response_format_validates_against_the_real_sdk_request_model
    -- proves the multimodal `input` shape (a `user_input` step wrapping
    text+image Content blocks) is genuinely accepted and typed by the
    installed google-genai SDK, not just by a permissive fake client. A
    bare list of content dicts (without the `user_input` step wrapper) is
    silently misparsed as unrecognized "steps" by this SDK version -- this
    guards against that regression.
    """
    from google.genai.interactions import CreateModelInteraction
    from google.genai._gaos.types.interactions.userinputstep import UserInputStep
    from google.genai._gaos.types.interactions.textcontent import TextContent
    from google.genai._gaos.types.interactions.imagecontent import ImageContent

    interactions = _FakeInteractions(output_text='{"computer_domain": true}')
    provider = _provider(interactions)

    provider.evaluate_visual_candidate(b"\xff\xd8fakejpeg", "image/jpeg", "Evaluate this image.")

    call = interactions.calls[0]
    validated = CreateModelInteraction.model_validate(call)

    assert isinstance(validated.input[0], UserInputStep)
    assert isinstance(validated.input[0].content[0], TextContent)
    assert isinstance(validated.input[0].content[1], ImageContent)


def test_evaluate_visual_candidate_raises_vision_evaluation_error_on_empty_response():
    interactions = _FakeInteractions(output_text=None, status="failed")
    provider = _provider(interactions)

    with pytest.raises(VisionEvaluationError):
        provider.evaluate_visual_candidate(b"bytes", "image/jpeg", "prompt")


def test_ping_and_generate_script_errors_are_distinct_types():
    """Regression guard for "do not collapse every failure into only
    ScriptGenerationError" -- ping() and generate_script() must raise
    different exception types on the same underlying empty-response failure."""
    ping_interactions = _FakeInteractions(output_text=None)
    script_interactions = _FakeInteractions(output_text=None)

    with pytest.raises(GeminiPingError):
        _provider(ping_interactions).ping()

    with pytest.raises(ScriptGenerationError):
        _provider(script_interactions).generate_script("prompt")

    assert not issubclass(GeminiPingError, ScriptGenerationError)
    assert not issubclass(ScriptGenerationError, GeminiPingError)


# ---------------------------------------------------------------------------
# 429 / rate-limit resilience -- the incident this covers: GitHub Actions hit
# a real "Quota exceeded ... generate_content_free_tier_requests, limit: 20"
# HTTP 429 mid-run. No real SDK/network involved below: a plain fake
# exception with `.code`/`.response`/`.details` stands in for
# google.genai.errors.ClientError, since that duck-typed shape is all
# _is_rate_limited/_extract_retry_after_seconds/_call_with_retry actually
# look at (see providers/llm.py).
# ---------------------------------------------------------------------------


class _FakeHeaders(dict):
    def get(self, key, default=None):  # case-insensitive like real HTTP headers
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _FakeHttpResponse:
    def __init__(self, headers: dict | None = None):
        self.headers = _FakeHeaders(headers or {})


class _FakeRateLimitError(Exception):
    def __init__(self, *, retry_after_header: str | None = None, retry_delay_detail: str | None = None):
        super().__init__("429 RESOURCE_EXHAUSTED")
        self.code = 429
        self.response = _FakeHttpResponse({"Retry-After": retry_after_header}) if retry_after_header else None
        self.details = (
            {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay_detail}]}}
            if retry_delay_detail
            else None
        )


class _FlakyInteractions:
    """create() raises the given exceptions in order, then finally succeeds."""

    def __init__(self, exceptions_then_success: list[Exception], output_text: str = "OK") -> None:
        self._to_raise = list(exceptions_then_success)
        self._output_text = output_text
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        if self._to_raise:
            raise self._to_raise.pop(0)
        return SimpleNamespace(output_text=self._output_text, status="completed", errors=[])


def test_is_rate_limited_true_for_code_429():
    assert _is_rate_limited(_FakeRateLimitError()) is True


def test_is_rate_limited_false_for_other_errors():
    assert _is_rate_limited(ValueError("boom")) is False
    assert _is_rate_limited(GeminiPingError("no output")) is False


def test_extract_retry_after_seconds_from_http_header():
    exc = _FakeRateLimitError(retry_after_header="42")
    assert _extract_retry_after_seconds(exc) == 42.0


def test_extract_retry_after_seconds_from_retry_info_detail():
    exc = _FakeRateLimitError(retry_delay_detail="42s")
    assert _extract_retry_after_seconds(exc) == 42.0


def test_extract_retry_after_seconds_returns_none_when_absent():
    assert _extract_retry_after_seconds(_FakeRateLimitError()) is None


def test_generate_script_retries_on_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda s: sleeps.append(s))

    interactions = _FlakyInteractions([_FakeRateLimitError(), _FakeRateLimitError()], output_text='{"title": "T"}')
    provider = _provider(interactions)

    result = provider.generate_script("prompt")

    assert result == '{"title": "T"}'
    assert interactions.call_count == 3  # 2 failures + 1 success
    assert len(sleeps) == 2


def test_generate_script_respects_retry_after_header(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda s: sleeps.append(s))

    interactions = _FlakyInteractions([_FakeRateLimitError(retry_after_header="42")], output_text='{"title": "T"}')
    provider = _provider(interactions)

    provider.generate_script("prompt")

    assert len(sleeps) == 1
    # Honors the server's requested delay (plus jitter), not a shorter
    # exponential-backoff guess.
    assert 42.0 <= sleeps[0] <= 42.0 * 1.25 + 0.01


def test_generate_script_gives_up_after_max_retries_and_raises(monkeypatch):
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda s: None)

    # One more failure than the retry budget allows.
    interactions = _FlakyInteractions([_FakeRateLimitError() for _ in range(GEMINI_MAX_RETRIES + 1)])
    provider = _provider(interactions)

    with pytest.raises(Exception) as exc_info:
        provider.generate_script("prompt")

    assert _is_rate_limited(exc_info.value)
    assert interactions.call_count == GEMINI_MAX_RETRIES + 1


def test_generate_script_does_not_retry_non_rate_limit_errors(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda s: sleeps.append(s))

    class _Boom:
        def create(self, **kwargs):
            raise ValueError("not a rate limit error")

    provider = _provider(_Boom())

    with pytest.raises(ValueError):
        provider.generate_script("prompt")

    assert sleeps == []


def test_rate_limit_retry_logs_the_required_message(monkeypatch, caplog):
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda s: None)
    interactions = _FlakyInteractions([_FakeRateLimitError()], output_text='{"title": "T"}')
    provider = _provider(interactions)

    with caplog.at_level("WARNING"):
        provider.generate_script("prompt")

    assert any("Gemini rate limited, retrying in" in record.message for record in caplog.records)
    # Never logs anything secret (API key, request body, etc.) alongside it.
    assert "unused-with-fake-client" not in caplog.text


def test_evaluate_visual_candidate_also_retries_on_429(monkeypatch):
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda s: None)
    interactions = _FlakyInteractions([_FakeRateLimitError()], output_text='{"computer_domain": true}')
    provider = _provider(interactions)

    result = provider.evaluate_visual_candidate(b"bytes", "image/jpeg", "prompt")

    assert result == '{"computer_domain": true}'
    assert interactions.call_count == 2


# ---------------------------------------------------------------------------
# Real production incident: a GitHub Actions run hit a genuine 429 and the
# retry logic above did NOT fire at all. The actual exception the installed
# google-genai SDK (2.22.0) raises for a 429 is
# google.genai._gaos.lib.compat_errors.RateLimitError, constructed below
# exactly as the SDK itself would (real httpx.Request/httpx.Response, real
# parsed error body) -- not a hand-rolled fake. It has a `status_code`
# attribute (inherited from APIStatusError), NOT a `.code` attribute at
# all, which is exactly why the original `getattr(exc, "code", None) ==
# 429` check silently never matched it. See providers/llm.py's
# _is_rate_limited docstring for the fix and its priority order.
# ---------------------------------------------------------------------------

# Exact production message shape (a real Gemini free-tier quota response),
# including the literal "Please retry in 24.354420322s." sentence.
_REAL_QUOTA_MESSAGE = (
    "Quota exceeded for quota metric 'Generate Content API requests' and limit "
    "'GenerateContent request limit' of service 'generativelanguage.googleapis.com' "
    "for consumer 'project_number:123456789'. Please retry in 24.354420322s."
)


def _real_rate_limit_error(message: str = _REAL_QUOTA_MESSAGE) -> Exception:
    """Builds the exact exception type/shape the installed google-genai SDK
    raises for a 429 -- a real httpx.Request/httpx.Response and the SDK's
    own composed message, not a hand-rolled stand-in."""
    body = {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED"}}
    request = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1/interactions")
    response = httpx.Response(429, request=request, json=body)
    return _RealRateLimitError(f"Error code: 429 - {body}", response=response, body=body)


@requires_real_sdk_rate_limit_error
def test_real_sdk_rate_limit_error_has_no_code_attribute_documenting_the_original_bug():
    """Documents the actual installed SDK shape: RateLimitError has
    `status_code` (429) but genuinely no `code` attribute -- proving why
    the old `.code == 429` check could never have worked against it."""
    exc = _real_rate_limit_error()

    assert exc.status_code == 429
    assert not hasattr(exc, "code")
    assert isinstance(exc.body, dict)


@requires_real_sdk_rate_limit_error
def test_is_rate_limited_recognizes_the_real_sdk_exception_type():
    assert _is_rate_limited(_real_rate_limit_error()) is True


@requires_real_sdk_rate_limit_error
def test_extract_retry_after_seconds_parses_the_real_quota_message():
    delay = _extract_retry_after_seconds(_real_rate_limit_error())
    assert delay is not None
    assert delay == pytest.approx(24.354420322, abs=1e-6)


@requires_real_sdk_rate_limit_error
def test_generate_script_retries_on_the_real_sdk_rate_limit_error_then_succeeds(monkeypatch):
    """The end-to-end regression test: first call raises the real-style 429,
    _is_rate_limited recognizes it, a retry delay is extracted from the
    message, the sleep/backoff path is invoked, the second call succeeds,
    and generate_script() returns the successful result."""
    sleeps: list[float] = []
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda s: sleeps.append(s))

    interactions = _FlakyInteractions([_real_rate_limit_error()], output_text='{"title": "Recovered"}')
    provider = _provider(interactions)

    result = provider.generate_script("prompt")

    assert result == '{"title": "Recovered"}'
    assert interactions.call_count == 2  # 1 failure (the real 429) + 1 success
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(24.354420322, rel=0.30)  # allows for jitter, still well under the 60s cap
    assert sleeps[0] <= 60.0


def test_is_rate_limited_regression_matches_the_exact_production_error_text():
    """Regression guard for the exact strings from the real incident report
    -- proven here with a bare exception carrying ONLY a message (no
    `.code`, `.status_code`, `.response`, `.body`, or `.details` at all),
    so this specifically exercises the sanitized text-fallback tier, not
    any structured attribute."""

    class _BareException(Exception):
        pass

    exc = _BareException(
        "Error code: 429 - {'error': {'code': 429, 'status': 'too_many_requests', "
        "'message': \"Quota exceeded... Please retry in 24.354420322s.\"}}"
    )

    assert _is_rate_limited(exc) is True
    delay = _extract_retry_after_seconds(exc)
    assert delay is not None
    assert delay == pytest.approx(24.354420322, abs=1e-6)


def test_generate_content_brief_returns_text_and_uses_real_sdk_schema():
    from google.genai.interactions import CreateModelInteraction, TextResponseFormat
    from scripts.production.providers.llm import CONTENT_BRIEF_SCHEMA

    interactions = _FakeInteractions(output_text='{"target_audience": "Gamers"}')
    assert _provider(interactions).generate_content_brief("brief prompt") == interactions._output_text
    call = interactions.calls[0]
    assert call == {
        "model": DEFAULT_GEMINI_MODEL, "input": "brief prompt",
        "response_format": {"type": "text", "mime_type": "application/json", "schema": CONTENT_BRIEF_SCHEMA},
    }
    validated = CreateModelInteraction.model_validate(call)
    assert isinstance(validated.response_format, TextResponseFormat)
    assert validated.response_format.schema_ == CONTENT_BRIEF_SCHEMA
    wire = validated.model_dump(by_alias=True, exclude_none=True)
    assert wire["response_format"]["schema"] == CONTENT_BRIEF_SCHEMA
    assert "schema_" not in wire["response_format"]


def test_generate_content_brief_empty_response_fails():
    with pytest.raises(ScriptGenerationError):
        _provider(_FakeInteractions(output_text="")).generate_content_brief("prompt")


def test_generate_content_brief_retries_rate_limit(monkeypatch):
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda _: None)
    interactions = _FlakyInteractions([_FakeRateLimitError()], output_text="{}")
    assert _provider(interactions).generate_content_brief("prompt") == "{}"
    assert interactions.call_count == 2
