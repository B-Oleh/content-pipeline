"""telegram_approval.py tests -- a fake TelegramClient stands in for the
real Bot API (no network), covering: the keyboard/callback_data shape,
malformed-callback rejection, the full approve/reject/regenerate/timeout
decision loop (including that unrelated/stale callbacks are answered but
ignored), approval-state persistence, and the GitHub workflow-dispatch
call used by "Regenerate" (mocked requests.post).
"""

from __future__ import annotations

import json

import pytest
import requests

from scripts.production.providers.telegram_client import TelegramClient
from scripts.production.telegram_approval import (
    APPROVE_ACTION,
    REGENERATE_ACTION,
    REJECT_ACTION,
    ApprovalState,
    RegenerationTriggerError,
    build_approval_keyboard,
    load_approval_state,
    parse_callback_data,
    poll_for_decision,
    save_approval_state,
    trigger_regeneration_workflow,
)

SECRET_TOKEN = "123456:AAsecret-bot-token-value"


# ---------------------------------------------------------------------------
# Keyboard construction and callback_data parsing
# ---------------------------------------------------------------------------


def test_build_approval_keyboard_contains_all_three_buttons():
    keyboard = build_approval_keyboard("content-42")
    buttons = keyboard["inline_keyboard"][0]

    assert len(buttons) == 3
    texts = {button["text"] for button in buttons}
    assert texts == {"✅ Approve", "\U0001f504 Regenerate", "❌ Reject"}


def test_build_approval_keyboard_callback_data_maps_to_the_correct_content_id():
    keyboard = build_approval_keyboard("content-42")
    buttons = keyboard["inline_keyboard"][0]

    callback_data_by_action = {btn["callback_data"].split(":")[0]: btn["callback_data"] for btn in buttons}
    assert callback_data_by_action["approve"] == "approve:content-42"
    assert callback_data_by_action["regenerate"] == "regenerate:content-42"
    assert callback_data_by_action["reject"] == "reject:content-42"


def test_parse_callback_data_valid():
    assert parse_callback_data("approve:content-42") == ("approve", "content-42")
    assert parse_callback_data("regenerate:abc-123") == ("regenerate", "abc-123")
    assert parse_callback_data("reject:xyz") == ("reject", "xyz")


def test_parse_callback_data_rejects_missing_separator():
    with pytest.raises(ValueError):
        parse_callback_data("approve")


def test_parse_callback_data_rejects_unknown_action():
    with pytest.raises(ValueError):
        parse_callback_data("publish:content-42")


def test_parse_callback_data_rejects_empty_content_id():
    with pytest.raises(ValueError):
        parse_callback_data("approve:")


# ---------------------------------------------------------------------------
# Approval-state persistence
# ---------------------------------------------------------------------------


def test_save_and_load_approval_state_round_trips(tmp_path):
    path = tmp_path / "approval_state.json"
    save_approval_state(path, ApprovalState(content_id="c1", decision="approve", detail="ok"))

    loaded = load_approval_state(path)

    assert loaded == ApprovalState(content_id="c1", decision="approve", detail="ok")


def test_load_approval_state_returns_none_when_missing(tmp_path):
    assert load_approval_state(tmp_path / "does_not_exist.json") is None


# ---------------------------------------------------------------------------
# poll_for_decision -- fake TelegramClient, no network
# ---------------------------------------------------------------------------


class _FakeTelegramClient:
    def __init__(self, update_batches: list[list[dict]]) -> None:
        self._update_batches = list(update_batches)
        self.answered: list[tuple[str, str]] = []
        self.messages_sent: list[str] = []

    def get_updates(self, offset=None, timeout=25):
        if not self._update_batches:
            return []
        return self._update_batches.pop(0)

    def answer_callback_query(self, callback_query_id, text=""):
        self.answered.append((callback_query_id, text))
        return {}

    def send_message(self, text):
        self.messages_sent.append(text)
        return {}


def _callback_update(update_id: int, callback_id: str, data: str) -> dict:
    return {"update_id": update_id, "callback_query": {"id": callback_id, "data": data}}


def test_poll_for_decision_approve_changes_state():
    client = _FakeTelegramClient([[_callback_update(1, "cb1", "approve:content-42")]])

    state = poll_for_decision(client, "content-42", timeout_seconds=5, long_poll_seconds=1)

    assert state.decision == APPROVE_ACTION
    assert state.content_id == "content-42"
    assert client.answered == [("cb1", "✅ Approved")]
    assert client.messages_sent == ["✅ Approved"]


def test_poll_for_decision_reject_changes_state():
    client = _FakeTelegramClient([[_callback_update(1, "cb1", "reject:content-42")]])

    state = poll_for_decision(client, "content-42", timeout_seconds=5, long_poll_seconds=1)

    assert state.decision == REJECT_ACTION
    assert client.messages_sent == ["❌ Rejected"]


def test_poll_for_decision_regenerate_changes_state():
    client = _FakeTelegramClient([[_callback_update(1, "cb1", "regenerate:content-42")]])

    state = poll_for_decision(client, "content-42", timeout_seconds=5, long_poll_seconds=1)

    assert state.decision == REGENERATE_ACTION
    assert client.messages_sent == ["\U0001f504 Regeneration started"]


def test_poll_for_decision_ignores_callback_for_a_different_content_id_but_still_answers_it():
    client = _FakeTelegramClient(
        [
            [_callback_update(1, "cb-stale", "approve:some-other-content")],
            [_callback_update(2, "cb-real", "approve:content-42")],
        ]
    )

    state = poll_for_decision(client, "content-42", timeout_seconds=5, long_poll_seconds=1)

    assert state.decision == APPROVE_ACTION
    # The stale callback's spinner was cleared even though it was ignored.
    assert ("cb-stale", "") in client.answered


def test_poll_for_decision_ignores_malformed_callback_data():
    client = _FakeTelegramClient(
        [
            [_callback_update(1, "cb-bad", "not-a-valid-callback")],
            [_callback_update(2, "cb-real", "approve:content-42")],
        ]
    )

    state = poll_for_decision(client, "content-42", timeout_seconds=5, long_poll_seconds=1)

    assert state.decision == APPROVE_ACTION


def test_poll_for_decision_times_out_when_nothing_arrives():
    client = _FakeTelegramClient([[], [], []])

    state = poll_for_decision(client, "content-42", timeout_seconds=0.2, long_poll_seconds=1)

    assert state.decision == "timeout"
    assert state.content_id == "content-42"


def test_poll_for_decision_advances_offset_past_seen_updates():
    seen_offsets = []

    class _OffsetTrackingClient(_FakeTelegramClient):
        def get_updates(self, offset=None, timeout=25):
            seen_offsets.append(offset)
            return super().get_updates(offset=offset, timeout=timeout)

    client = _OffsetTrackingClient([[_callback_update(5, "cb1", "approve:content-42")]])

    poll_for_decision(client, "content-42", timeout_seconds=5, long_poll_seconds=1)

    assert seen_offsets[0] is None  # first call has no offset yet


# ---------------------------------------------------------------------------
# TelegramClient additions used by the above (get_updates, answer_callback_query,
# send_message, send_video's reply_markup) -- real client, mocked HTTP.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, ok=True):
        self.status_code = status_code
        self.ok = ok
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def test_send_video_includes_reply_markup_as_json_string(monkeypatch, tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")
    captured = {}

    def fake_post(url, data=None, files=None, timeout=None):
        captured["data"] = data
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.post", fake_post)
    keyboard = build_approval_keyboard("content-42")

    TelegramClient(SECRET_TOKEN, "12345").send_video(video_path, "caption", reply_markup=keyboard)

    assert json.loads(captured["data"]["reply_markup"]) == keyboard


def test_get_updates_parses_result_list(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert params["timeout"] == 25
        return _FakeResponse(200, {"ok": True, "result": [{"update_id": 1}]})

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.get", fake_get)

    updates = TelegramClient(SECRET_TOKEN, "12345").get_updates()

    assert updates == [{"update_id": 1}]


def test_get_updates_passes_offset_when_given(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(200, {"ok": True, "result": []})

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.get", fake_get)

    TelegramClient(SECRET_TOKEN, "12345").get_updates(offset=7)

    assert captured["params"]["offset"] == 7


def test_answer_callback_query_success(monkeypatch):
    def fake_post(url, data=None, timeout=None):
        assert data["callback_query_id"] == "cb1"
        return _FakeResponse(200, {"ok": True, "result": True})

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.post", fake_post)

    TelegramClient(SECRET_TOKEN, "12345").answer_callback_query("cb1", "done")


def test_send_message_success(monkeypatch):
    def fake_post(url, data=None, timeout=None):
        assert data["text"] == "✅ Approved"
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 2}})

    monkeypatch.setattr("scripts.production.providers.telegram_client.requests.post", fake_post)

    result = TelegramClient(SECRET_TOKEN, "12345").send_message("✅ Approved")

    assert result["message_id"] == 2


# ---------------------------------------------------------------------------
# trigger_regeneration_workflow -- mocked GitHub REST API call
# ---------------------------------------------------------------------------


def test_trigger_regeneration_workflow_posts_to_the_dispatch_endpoint(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return type("R", (), {"status_code": 204, "text": ""})()

    monkeypatch.setattr("scripts.production.telegram_approval.requests.post", fake_post)

    trigger_regeneration_workflow(topic_title="Best budget GPUs", github_token="gh-token-secret", github_repository="owner/repo")

    assert captured["url"] == "https://api.github.com/repos/owner/repo/actions/workflows/produce_video.yml/dispatches"
    assert captured["headers"]["Authorization"] == "Bearer gh-token-secret"
    assert captured["json"]["inputs"]["topic_override"] == "Best budget GPUs"


def test_trigger_regeneration_workflow_raises_on_http_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return type("R", (), {"status_code": 404, "text": "Not Found"})()

    monkeypatch.setattr("scripts.production.telegram_approval.requests.post", fake_post)

    with pytest.raises(RegenerationTriggerError):
        trigger_regeneration_workflow(topic_title="Best budget GPUs", github_token="gh-token-secret", github_repository="owner/repo")


def test_trigger_regeneration_workflow_raises_on_network_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr("scripts.production.telegram_approval.requests.post", fake_post)

    with pytest.raises(RegenerationTriggerError):
        trigger_regeneration_workflow(topic_title="Best budget GPUs", github_token="gh-token-secret", github_repository="owner/repo")
