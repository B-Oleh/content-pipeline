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

import pytest

from scripts.production.providers.llm import (
    DEFAULT_GEMINI_MODEL,
    GeminiPingError,
    GeminiProvider,
    ScriptGenerationError,
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
