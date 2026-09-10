"""Shared logging setup for pipeline stages.

Every stage should call get_logger(__name__) instead of using print()
or configuring logging itself, so log format and destination stay
consistent across the pipeline.
"""

import logging

from scripts.config import LOG_LEVEL, LOGS_DIR

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Return a logger that writes to both the console and logs/<name>.log."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)
    formatter = logging.Formatter(_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(LOGS_DIR / f"{name}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
