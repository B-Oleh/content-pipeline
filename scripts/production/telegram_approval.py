"""Real Telegram Approve/Regenerate/Reject handling (CLAUDE.md "Telegram
approval gate").

**Why polling from inside the same GitHub Actions run, not a webhook.**
Telegram button clicks only reach a bot via one of two mechanisms: a
webhook (an HTTPS endpoint Telegram calls the instant a button is
pressed), or long polling (`getUpdates`). A webhook needs an
always-listening HTTPS server; GitHub Actions runners are ephemeral --
they exist only for the duration of one job -- so there is nowhere for a
webhook to be received once `produce_video.yml`'s job ends. Standing up a
persistent webhook receiver would mean adding a new always-on hosting
service (a paid or new free-tier account outside this repository's
existing $0/GitHub-Actions architecture), which CLAUDE.md "Cloud execution
and budget" and this task both say not to do without that being explicitly
revisited first.

The alternative the task explicitly named ("implement the smallest
practical callback-handling mechanism compatible with the current GitHub
Actions architecture") is what this module does: right after the video and
its buttons are sent, the SAME workflow run calls Telegram's own
`getUpdates` long-poll endpoint in a bounded loop. Each call blocks
server-side for up to `DEFAULT_LONG_POLL_TIMEOUT_SECONDS` (25s) waiting for
a new update before returning, so a 20-minute window costs on the order of
~48 HTTP requests total, not a tight busy loop (see CLAUDE.md "favor
event-driven ... over polling loops" -- this is a bounded wait attached to
one manual run, not a recurring scheduled poll job). The real, honest
limitation: a click that happens AFTER the window closes (the workflow run
has already ended) is not handled by this run. Re-running
`python -m scripts.produce_video --poll-only` (see produce_video.py) against
the same content_id is the documented way to pick up a late decision
without redesigning the architecture into a persistent service.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from scripts.production.providers.telegram_client import TelegramClient
from scripts.utils.atomic_write import atomic_write_text
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

APPROVE_ACTION = "approve"
REGENERATE_ACTION = "regenerate"
REJECT_ACTION = "reject"
_VALID_ACTIONS = {APPROVE_ACTION, REGENERATE_ACTION, REJECT_ACTION}

_CONFIRMATION_TEXT = {
    APPROVE_ACTION: "✅ Approved",
    REJECT_ACTION: "❌ Rejected",
    REGENERATE_ACTION: "\U0001f504 Regeneration started",
}

# How long one workflow run is willing to wait for a decision before giving
# up -- see module docstring for why this is bounded rather than
# indefinite. Comfortably inside produce_video.yml's job timeout.
DEFAULT_POLL_TIMEOUT_SECONDS = 1200  # 20 minutes


class RegenerationTriggerError(RuntimeError):
    """Raised when triggering a new produce_video.yml run via the GitHub
    REST API fails -- callers must report this honestly rather than
    claiming regeneration started when it did not (see the task's "do not
    silently fall back to non-functional buttons" rule)."""


@dataclass
class ApprovalState:
    content_id: str
    decision: str  # "pending" | "approve" | "regenerate" | "reject" | "timeout"
    detail: str = ""

    def to_dict(self) -> dict:
        return {"content_id": self.content_id, "decision": self.decision, "detail": self.detail}


def build_approval_keyboard(content_id: str) -> dict:
    """One Telegram inline_keyboard row with all three actions, each
    callback_data carrying the stable content_id so a later click can be
    mapped back to the exact video it was sent with (see
    parse_callback_data)."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"{APPROVE_ACTION}:{content_id}"},
                {"text": "\U0001f504 Regenerate", "callback_data": f"{REGENERATE_ACTION}:{content_id}"},
                {"text": "❌ Reject", "callback_data": f"{REJECT_ACTION}:{content_id}"},
            ]
        ]
    }


def parse_callback_data(data: str) -> tuple[str, str]:
    """Returns (action, content_id). Raises ValueError for anything not
    shaped like `action:content_id` with a recognized action -- a stray or
    malformed callback must never be silently treated as approve/reject."""
    if ":" not in data:
        raise ValueError(f"Malformed callback_data (no ':'): {data!r}")
    action, content_id = data.split(":", 1)
    if action not in _VALID_ACTIONS:
        raise ValueError(f"Unrecognized callback action {action!r} in {data!r}")
    if not content_id:
        raise ValueError(f"Empty content_id in callback_data: {data!r}")
    return action, content_id


def save_approval_state(path: Path, state: ApprovalState) -> None:
    """Records the decision as plain transient JSON under the run's own
    workdir (same place as script.json/qa_result.json) -- not committed to
    Git. This module still knows nothing about YouTube: pipeline.py reads
    `decision == "approve"` and drives the actual upload via
    youtube_publishing.py/providers/youtube.py, kept separate on purpose
    (see those modules' docstrings)."""
    atomic_write_text(path, json.dumps(state.to_dict(), indent=2))


def load_approval_state(path: Path) -> Optional[ApprovalState]:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return ApprovalState(content_id=data["content_id"], decision=data["decision"], detail=data.get("detail", ""))


def poll_for_decision(
    client: TelegramClient,
    content_id: str,
    *,
    timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    long_poll_seconds: int = 25,
) -> ApprovalState:
    """Blocks (via Telegram's own server-side long polling -- see module
    docstring) until a callback_query for `content_id` arrives or
    `timeout_seconds` elapses.

    Every callback_query received is answered (clears the button's
    "loading" spinner for the clicking user) even if it does not match
    `content_id` or is malformed -- an unrelated/stale button must never be
    left spinning forever on the user's screen. Only a match for
    `content_id` ends the wait.
    """
    deadline = time.monotonic() + timeout_seconds
    offset: Optional[int] = None

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        this_poll_timeout = max(1, min(long_poll_seconds, int(remaining)))
        try:
            updates = client.get_updates(offset=offset, timeout=this_poll_timeout)
        except Exception as exc:  # noqa: BLE001 -- a transient network hiccup must not abort the whole wait
            logger.warning("getUpdates failed, will retry: %s", exc)
            time.sleep(min(5, max(0, remaining)))
            continue

        for update in updates:
            offset = update["update_id"] + 1
            callback = update.get("callback_query")
            if not callback:
                continue

            raw_data = callback.get("data", "")
            try:
                action, callback_content_id = parse_callback_data(raw_data)
            except ValueError as exc:
                logger.warning("Ignoring malformed callback_data %r: %s", raw_data, exc)
                _safe_answer(client, callback["id"], "")
                continue

            if callback_content_id != content_id:
                logger.info("Ignoring callback for a different content_id (%s != %s)", callback_content_id, content_id)
                _safe_answer(client, callback["id"], "")
                continue

            _safe_answer(client, callback["id"], _CONFIRMATION_TEXT[action])
            if action != APPROVE_ACTION:
                # Reject/Regenerate: unchanged -- their confirmation text
                # never depends on anything that happens after this point,
                # so it is sent immediately, same as before.
                #
                # Approve is different: the real confirmation now depends
                # on whether the YouTube upload succeeds, which is only
                # known after this function returns (see
                # pipeline.py::_handle_approve). Sending the generic
                # "✅ Approved" here too would leave a stale/misleading
                # message in the chat once the richer upload-result message
                # follows, so it is deliberately skipped for this action.
                _safe_send_message(client, _CONFIRMATION_TEXT[action])
            logger.info("Received decision %r for content_id=%s", action, content_id)
            return ApprovalState(content_id=content_id, decision=action)

    logger.warning("No approval decision received for content_id=%s within %.0fs", content_id, timeout_seconds)
    return ApprovalState(content_id=content_id, decision="timeout", detail=f"no decision within {timeout_seconds:.0f}s")


def _safe_answer(client: TelegramClient, callback_query_id: str, text: str) -> None:
    try:
        client.answer_callback_query(callback_query_id, text)
    except Exception as exc:  # noqa: BLE001 -- failing to clear a spinner must not abort the poll loop
        logger.warning("answer_callback_query failed: %s", exc)


def _safe_send_message(client: TelegramClient, text: str) -> None:
    try:
        client.send_message(text)
    except Exception as exc:  # noqa: BLE001 -- a confirmation-message failure must not abort the poll loop
        logger.warning("send_message failed: %s", exc)


def trigger_regeneration_workflow(
    *,
    topic_title: str,
    github_token: str,
    github_repository: str,
    workflow_file: str = "produce_video.yml",
    ref: str = "main",
    timeout: float = 15.0,
) -> None:
    """Dispatches a new run of the SAME workflow via the GitHub REST API,
    passing the current topic's title through as an input so
    topic_selection.py can prefer re-selecting it (see that module's
    `preferred_title` parameter) -- this is "the same topic/content intent"
    the task asks for, achieved without a second workflow or new
    infrastructure.

    Uses the default GITHUB_TOKEN: `workflow_dispatch` is an explicit,
    documented exception to GitHub's usual "events triggered by
    GITHUB_TOKEN do not start new workflow runs" recursion guard, so this
    does not need a separate personal access token.
    """
    url = f"https://api.github.com/repos/{github_repository}/actions/workflows/{workflow_file}/dispatches"
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
            },
            json={"ref": ref, "inputs": {"topic_override": topic_title}},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as exc:
        raise RegenerationTriggerError(f"GitHub workflow dispatch request failed ({type(exc).__name__})") from None

    if response.status_code >= 300:
        raise RegenerationTriggerError(
            f"GitHub workflow dispatch failed with HTTP {response.status_code}: {response.text[:300]}"
        )
