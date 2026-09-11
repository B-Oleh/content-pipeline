"""CLI entry point for the video production pipeline.

Usage:
    python -m scripts.produce_video

Reads GEMINI_API_KEY, PEXELS_API_KEY, PIXABAY_API_KEY, TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET,
YOUTUBE_REFRESH_TOKEN from the environment (GitHub Secrets in CI, .env
locally -- see CLAUDE.md "Environment variables and secret handling").
Never logs their values. See docs/PRODUCTION_PIPELINE.md for the full
pipeline design.

Also reads three optional environment variables used only by the Telegram
"Regenerate" button (see telegram_approval.py):
- GITHUB_TOKEN: the workflow's own default token (needs `actions: write`
  permission -- see produce_video.yml), used to trigger a new run of this
  same workflow. Not a new secret to create -- GitHub Actions provides this
  automatically to every workflow run.
- GITHUB_REPOSITORY: also provided automatically by GitHub Actions
  (`owner/repo`); not needed locally.
- TOPIC_OVERRIDE: set by produce_video.yml from the workflow_dispatch
  `topic_override` input when a run was started by a Regenerate click, so
  topic_selection.py can prefer re-selecting the same topic.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from scripts.config import DATA_DIR, OUTPUT_DIR
from scripts.production.preflight import PreflightError, run_preflight
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

DEFAULT_WORKDIR = DATA_DIR / "production"
DEFAULT_OUTPUT_PATH = OUTPUT_DIR / "final_video.mp4"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the video production pipeline end to end.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Final MP4 path (default: output/final_video.mp4)")
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR, help="Working directory for intermediate files (default: data/production)")
    args = parser.parse_args(argv)

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    pexels_api_key = os.getenv("PEXELS_API_KEY")
    pixabay_api_key = os.getenv("PIXABAY_API_KEY")
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    github_token = os.getenv("GITHUB_TOKEN")
    github_repository = os.getenv("GITHUB_REPOSITORY")
    topic_override = os.getenv("TOPIC_OVERRIDE") or None
    youtube_client_id = os.getenv("YOUTUBE_CLIENT_ID")
    youtube_client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    youtube_refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")

    logger.info("Running preflight checks")
    try:
        run_preflight(
            gemini_api_key=gemini_api_key,
            pexels_api_key=pexels_api_key,
            pixabay_api_key=pixabay_api_key,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
            youtube_client_id=youtube_client_id,
            youtube_client_secret=youtube_client_secret,
            youtube_refresh_token=youtube_refresh_token,
        )
    except PreflightError as exc:
        logger.error(str(exc))
        print(f"PREFLIGHT FAILED: {exc}")
        sys.exit(1)

    # Imported after preflight so a missing/invalid secret fails fast
    # without importing heavier dependencies (google-genai, edge-tts) first.
    from scripts.production.pipeline import run_pipeline

    result = run_pipeline(
        gemini_api_key=gemini_api_key,
        pexels_api_key=pexels_api_key,
        pixabay_api_key=pixabay_api_key,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        workdir=args.workdir,
        output_path=args.output,
        topic_override=topic_override,
        github_token=github_token,
        github_repository=github_repository,
        youtube_client_id=youtube_client_id,
        youtube_client_secret=youtube_client_secret,
        youtube_refresh_token=youtube_refresh_token,
    )

    print(f"Video: {result.video_path}")
    print(f"QA passed: {result.qa_result.passed}")
    for line in result.qa_result.summary_lines():
        print(f"  {line}")
    print(f"Delivered to Telegram: {result.delivered}")
    if result.approval is not None:
        print(f"Approval decision: {result.approval.decision} ({result.approval.detail})")

    if not result.qa_result.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
