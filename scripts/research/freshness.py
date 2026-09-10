"""Explicit freshness classification for Research Agent V0.2.

Freshness here means only "how recently was this source item published" --
it is evidence that the underlying story/topic is currently active, never a
proxy for popularity, importance, or virality (see CLAUDE.md "Research and
opportunity scoring rules" -- do not fabricate demand/popularity signals).

Thresholds are a small, explicit, documented table -- not a statistical
model -- so they are easy to retune as the niche's real publishing cadence
becomes clearer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from scripts.research.models import FreshnessTier

# (max_age_days, tier) -- checked in order; the first matching bound wins.
# Configurable: tune these as needed, independently of scoring.py or ranking.py.
FRESHNESS_RULES: list[tuple[float, FreshnessTier]] = [
    (2.0, FreshnessTier.VERY_RECENT),
    (14.0, FreshnessTier.RECENT),
]
DEFAULT_TIER = FreshnessTier.OLDER_EVERGREEN

FRESHNESS_SCORES: dict[FreshnessTier, float] = {
    FreshnessTier.VERY_RECENT: 9.0,
    FreshnessTier.RECENT: 7.0,
    FreshnessTier.OLDER_EVERGREEN: 5.0,
    # No verified publish date -- neutral default, not a guess at recency.
    FreshnessTier.UNKNOWN: 5.0,
}


def classify_age(age_days: Optional[float]) -> FreshnessTier:
    """Map an age in days to a FreshnessTier using FRESHNESS_RULES."""
    if age_days is None or age_days < 0:
        return FreshnessTier.UNKNOWN
    for max_age_days, tier in FRESHNESS_RULES:
        if age_days <= max_age_days:
            return tier
    return DEFAULT_TIER


def classify_published_at(
    published_at_raw: Optional[str], as_of: Optional[datetime] = None
) -> tuple[FreshnessTier, Optional[float]]:
    """Classify an ISO-8601 publish timestamp; returns (tier, age_in_days).

    Returns (UNKNOWN, None) for a missing or unparsable timestamp rather than
    raising -- freshness is best-effort evidence, not a required field.
    """
    if not published_at_raw:
        return FreshnessTier.UNKNOWN, None
    try:
        published_at = datetime.fromisoformat(published_at_raw)
    except (TypeError, ValueError):
        return FreshnessTier.UNKNOWN, None
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    as_of = as_of or datetime.now(timezone.utc)
    age_days = (as_of - published_at).total_seconds() / 86400
    return classify_age(age_days), age_days


def freshness_score(tier: FreshnessTier) -> float:
    """The 0-10 scoring.py dimension value for a given tier."""
    return FRESHNESS_SCORES[tier]
