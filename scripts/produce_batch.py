"""Overnight batch mode: deliver candidates for morning review without approval
polling or automatic publishing. Uses the same environment secrets as produce_video.

Usage: python -m scripts.produce_batch --batch-size 3
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from scripts.config import DATA_DIR, OUTPUT_DIR
from scripts.production.preflight import PreflightError, _safe_error_message, run_preflight
from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Produce an overnight batch for Telegram review.")
    parser.add_argument("--workdir", type=Path, default=DATA_DIR / "production")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR / "batch")
    parser.add_argument("--batch-size", type=int, default=3)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    secrets = {name: os.getenv(name.upper()) for name in (
        "gemini_api_key", "pexels_api_key", "pixabay_api_key", "telegram_bot_token",
        "telegram_chat_id", "youtube_client_id", "youtube_client_secret", "youtube_refresh_token",
    )}
    logger.info("Running preflight checks")
    try:
        run_preflight(**secrets)
    except PreflightError as exc:
        message = _safe_error_message(exc)
        logger.error(message)
        print(f"PREFLIGHT FAILED: {message}")
        sys.exit(1)

    from scripts.production.batch import run_batch

    result = run_batch(
        **{name: value for name, value in secrets.items() if not name.startswith("youtube_")},
        workdir=args.workdir, output_dir=args.output_dir, batch_size=args.batch_size,
    )
    print(f"Delivered to Telegram: {len(result.completed)}/{result.batch_size}")
    for candidate in result.completed:
        print(
            f"Candidate {candidate.candidate_number}: {candidate.topic}\n"
            f"  Hook: {candidate.content_brief.selected_hook}\n"
            f"  Real-media coverage: {candidate.real_media_coverage:.0%}; "
            f"duration: {candidate.duration_seconds:.1f}s; QA: {candidate.qa_passed}"
        )
    if result.blocker:
        print(f"Blocker: {result.blocker}")
    if not result.completed:
        sys.exit(1)


if __name__ == "__main__":
    main()
