"""GeminiRateLimiter unit tests -- the shared pacing/saturation coordinator
that keeps all Gemini calls (Script, content brief, Vision) inside the free-tier
20-requests-per-minute window and makes a seen 429 delay *every* call, not just
the one that received it (see providers/llm.py and docs/PRODUCTION_PIPELINE.md
"Gemini quota coordination").

All sleeps are monkeypatched at
`scripts.production.providers.llm.time.sleep` (the same attribute the
provider retry tests patch), so these tests are instant and only assert the
durations the limiter *requests*.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.production.providers.llm import (
    GEMINI_RATE_LIMIT_MAX_PER_WINDOW,
    GEMINI_RATE_LIMIT_MIN_INTERVAL_SECONDS,
    GEMINI_RATE_LIMIT_SATURATION_MARGIN_SECONDS,
    GeminiProvider,
    GeminiRateLimiter,
)


def _record_sleeps(monkeypatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr("scripts.production.providers.llm.time.sleep", lambda s: sleeps.append(s))
    return sleeps


def test_first_call_never_waits(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    limiter = GeminiRateLimiter()
    limiter.wait_until_allowed()
    assert sleeps == []


def test_min_interval_paces_a_second_success(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    limiter = GeminiRateLimiter(min_interval_seconds=1.0, max_requests_per_window=100)
    limiter.record_success()
    limiter.wait_until_allowed()
    assert len(sleeps) == 1
    # Approximately the minimum interval (plus nothing else -- window is huge).
    assert 0.9 <= sleeps[0] <= 1.1


def test_no_min_interval_wait_when_the_last_success_was_long_ago(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    limiter = GeminiRateLimiter(min_interval_seconds=1.0, max_requests_per_window=100)
    # Simulate the last success being well beyond the interval: seed the
    # window with an old timestamp and let wait_until_allowed prune it.
    limiter._request_times.append(float("-inf"))
    limiter.wait_until_allowed()
    assert sleeps == []


def test_window_capacity_blocks_until_a_slot_frees(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    limiter = GeminiRateLimiter(
        max_requests_per_window=2, window_seconds=60.0, min_interval_seconds=0.0,
    )
    limiter.record_success()
    limiter.record_success()
    limiter.wait_until_allowed()
    # The window is full (2/2): the wait is the oldest request's age-out,
    # ~window_seconds, plus the saturation margin.
    assert sleeps == [pytest.approx(60.0 + GEMINI_RATE_LIMIT_SATURATION_MARGIN_SECONDS, abs=1.0)]


def test_saturation_from_rate_limit_blocks_all_callers(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    limiter = GeminiRateLimiter()
    limiter.record_rate_limit(30.0)
    limiter.wait_until_allowed()
    # A 429 said "retry in 30s", so the next caller must wait ~30s too.
    assert sleeps == [pytest.approx(30.0 + GEMINI_RATE_LIMIT_SATURATION_MARGIN_SECONDS, abs=1.0)]


def test_record_rate_limit_none_is_a_no_op(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    limiter = GeminiRateLimiter()
    limiter.record_rate_limit(None)
    limiter.wait_until_allowed()
    assert sleeps == []


def test_wait_bounded_by_max_single_wait(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    limiter = GeminiRateLimiter(max_single_wait_seconds=5.0, window_seconds=60.0, min_interval_seconds=0.0, max_requests_per_window=2)
    limiter.record_success()
    limiter.record_success()
    limiter.wait_until_allowed()
    assert sleeps == [5.0]


def test_defaults_match_the_free_tier_20_per_minute(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    limiter = GeminiRateLimiter()
    # 19 successes a minute apart-as-allowed would still be legal, but the
    # static constants document the intent clearly.
    assert GEMINI_RATE_LIMIT_MAX_PER_WINDOW == 20
    # Sustained rate = window / min_interval is comfortably under 20/min.
    assert 60.0 / GEMINI_RATE_LIMIT_MIN_INTERVAL_SECONDS < GEMINI_RATE_LIMIT_MAX_PER_WINDOW
    # And a bare limiter paces but never waits on the very first call.
    limiter.wait_until_allowed()
    assert sleeps == []


class _FakeInteractions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(output_text='{"title": "T"}', status="completed", errors=[])


def test_provider_script_and_vision_share_one_limiter(monkeypatch):
    """A single GeminiProvider instance paces BOTH generate_script and
    evaluate_visual_candidate against the same limiter -- the coordination
    the overnight-batch fix needs (Script + content brief + Vision must not
    fire back-to-back)."""
    sleeps = _record_sleeps(monkeypatch)
    interactions = _FakeInteractions()
    provider = GeminiProvider(
        api_key="offline",
        client=SimpleNamespace(interactions=interactions),
        rate_limiter=GeminiRateLimiter(min_interval_seconds=0.05, max_requests_per_window=100),
    )

    provider.generate_script("first")
    provider.evaluate_visual_candidate(b"bytes", "image/jpeg", "second")

    assert interactions.calls == 2
    # The second call was paced by the min interval (one sleep), i.e. the two
    # different call shapes share the same rate-limiter's window.
    assert len(sleeps) == 1
    assert 0.0 <= sleeps[0] <= 0.1