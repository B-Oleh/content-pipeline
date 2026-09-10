"""Guardrails for the video production GitHub Actions workflow -- same
approach as tests/test_github_workflow.py (plain text checks, no YAML
dependency; see that file's docstring for why)."""

from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "produce_video.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _workflow_code_text() -> str:
    return "\n".join(line for line in _workflow_text().splitlines() if not line.strip().startswith("#"))


def test_workflow_file_exists():
    assert WORKFLOW_PATH.exists()


def test_workflow_is_manually_triggered_only():
    assert "workflow_dispatch" in _workflow_text()


def test_workflow_never_adds_scheduling():
    text = _workflow_code_text().lower()
    assert "schedule:" not in text
    assert "cron" not in text
    assert "repository_dispatch" not in text


def test_workflow_declares_read_only_permissions():
    text = _workflow_code_text()
    assert "permissions:" in text
    assert "contents: read" in text
    for broad_scope in ("contents: write", "issues: write", "pull-requests: write", "id-token: write"):
        assert broad_scope not in text


def test_workflow_never_disables_tls_verification():
    text = _workflow_code_text().lower()
    for forbidden in ("verify=false", "cert_none", "check_hostname = false", "--insecure", "-k http"):
        assert forbidden not in text


def test_workflow_never_commits_or_pushes():
    text = _workflow_code_text()
    assert "git add" not in text
    assert "git commit" not in text
    assert "git push" not in text


def test_workflow_never_echoes_secrets_directly():
    text = _workflow_code_text()
    for secret in ("GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "TELEGRAM_BOT_TOKEN"):
        assert f"echo ${secret}" not in text
        assert f"echo ${{{secret}}}" not in text
        assert f"echo \"${secret}" not in text


def test_workflow_references_all_required_secrets():
    text = _workflow_text()
    for secret in ("GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert f"secrets.{secret}" in text


def test_workflow_has_a_concurrency_guard():
    assert "concurrency:" in _workflow_text()


def test_workflow_runs_tests_before_preflight_before_pipeline():
    text = _workflow_text()
    pytest_idx = text.find("pytest")
    preflight_idx = text.find("scripts.production.preflight")
    pipeline_idx = text.find("scripts.produce_video")
    assert -1 < pytest_idx < preflight_idx < pipeline_idx


def test_workflow_uploads_diagnostics_only_on_failure():
    text = _workflow_text()
    assert "if: failure()" in text
    assert "upload-artifact" in text
    assert "retention-days:" in text
