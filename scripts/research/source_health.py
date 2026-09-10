"""Source health classification and aggregation for Research Agent V0.2.

Every run tries several independent sources; some may be unreachable, rate
limited, or return malformed data. This must be reported, not hidden (see
the "Source health" requirement in docs/RESEARCH_AGENT.md) -- a run where
most sources failed produced fewer, less diverse candidates than a healthy
run, and that should be visible before anything gets produced from it.

SourceHealth itself (the per-source record) lives in models.py alongside the
other domain dataclasses; this module holds the logic that operates on it.
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import xml.etree.ElementTree as ET

from scripts.research.models import SourceHealth

# If at least this fraction of configured sources failed, the run is
# reported as degraded. Configurable -- tune as the real source mix grows.
DEGRADED_FAILURE_RATIO = 0.5


def classify_error(exc: Exception) -> str:
    """Bucket an exception into a small, stable set of categories for reporting."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLError):
            return "tls_error"
        return "network"
    if isinstance(exc, ET.ParseError):
        return "parse_error"
    if isinstance(exc, json.JSONDecodeError):
        return "parse_error"
    if isinstance(exc, FileNotFoundError):
        return "config_error"
    return "unknown"


def is_degraded(reports: list[SourceHealth]) -> bool:
    """True if no sources ran at all, or too many of them failed."""
    if not reports:
        return True
    failures = sum(1 for report in reports if not report.success)
    return (failures / len(reports)) >= DEGRADED_FAILURE_RATIO
