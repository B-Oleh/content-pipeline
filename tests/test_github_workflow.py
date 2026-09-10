"""Guardrails for the Research Agent GitHub Actions workflow.

These are plain text/string assertions rather than a YAML parse (no YAML
dependency exists in this project yet -- see CLAUDE.md "Adding
dependencies"), but they are enough to catch the specific regressions V0.3
must avoid: scheduling sneaking in, an insecure TLS bypass, overly broad
permissions, or staging more than state/ in the automated commit.
"""

from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "research_agent.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _workflow_code_text() -> str:
    """The workflow with full-line comments stripped.

    The workflow file deliberately documents in comments what it does NOT
    do (e.g. "No --force", "never git add -A") -- those explanatory comments
    would otherwise trip up naive substring checks below. This only strips
    lines that are comments after stripping leading whitespace; it is not a
    general YAML/shell parser and does not need to be one for these checks.
    """
    return "\n".join(line for line in _workflow_text().splitlines() if not line.strip().startswith("#"))


def test_workflow_file_exists():
    assert WORKFLOW_PATH.exists()


def test_workflow_is_manually_triggered_only():
    text = _workflow_text()
    assert "workflow_dispatch" in text


def test_workflow_never_adds_scheduling():
    text = _workflow_code_text().lower()
    assert "schedule:" not in text
    assert "cron" not in text
    assert "repository_dispatch" not in text


def test_workflow_never_mentions_telegram_or_llm_providers():
    text = _workflow_text().lower()
    for forbidden in ("telegram", "anthropic", "gemini", "openai"):
        assert forbidden not in text


def test_workflow_never_disables_tls_verification():
    text = _workflow_text().lower()
    for forbidden in ("verify=false", "cert_none", "check_hostname = false", "insecurerequestwarning", "--insecure", "-k http"):
        assert forbidden not in text


def test_workflow_declares_minimal_permissions():
    text = _workflow_text()
    assert "permissions:" in text
    assert "contents: write" in text
    # No broader scopes than needed to push the state commit.
    for broad_scope in ("issues: write", "pull-requests: write", "packages: write", "id-token: write"):
        assert broad_scope not in text


def test_workflow_stages_only_state_directory():
    text = _workflow_code_text()
    assert "git add state/" in text
    assert "git add -A" not in text
    assert "git add ." not in text


def test_workflow_commit_message_is_recognizable():
    text = _workflow_text()
    assert "chore(state): update research state" in text


def test_workflow_never_force_pushes():
    text = _workflow_code_text()
    assert "--force" not in text
    assert "-f " not in text


def test_workflow_has_a_concurrency_guard():
    text = _workflow_text()
    assert "concurrency:" in text


def test_workflow_uploads_transient_artifact_with_short_retention():
    text = _workflow_text()
    assert "upload-artifact" in text
    assert "retention-days:" in text


def test_workflow_runs_the_test_suite_before_the_agent():
    text = _workflow_text()
    pytest_index = text.find("pytest")
    agent_index = text.find("scripts.research_agent")
    assert pytest_index != -1
    assert agent_index != -1
    assert pytest_index < agent_index
