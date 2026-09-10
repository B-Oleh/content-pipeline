"""Central configuration shared by every pipeline stage.

Loads environment variables from .env (if present) and exposes the
shared directory paths so stage scripts don't hard-code locations.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Persistent pipeline state (e.g. game recommendation history) that must
# survive across separate runs/workflow executions -- unlike ASSETS_DIR,
# OUTPUT_DIR, DATA_DIR, and LOGS_DIR above, this directory is intentionally
# NOT git-ignored (see docs/RESEARCH_AGENT.md "Git-backed persistent state").
# Individual stages resolve their own subdirectory under this (e.g.
# scripts/research/cli.py uses STATE_DIR / "research"); relative to BASE_DIR
# like every other directory here, so it never hard-codes an absolute path
# and behaves identically locally and in CI.
STATE_DIR = BASE_DIR / "state"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

for _directory in (ASSETS_DIR, OUTPUT_DIR, DATA_DIR, LOGS_DIR, STATE_DIR):
    _directory.mkdir(parents=True, exist_ok=True)
