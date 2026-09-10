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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

for _directory in (ASSETS_DIR, OUTPUT_DIR, DATA_DIR, LOGS_DIR):
    _directory.mkdir(parents=True, exist_ok=True)
