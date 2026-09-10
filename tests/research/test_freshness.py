from datetime import datetime, timedelta, timezone

from scripts.research.freshness import classify_age, classify_published_at, freshness_score
from scripts.research.models import FreshnessTier

AS_OF = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_classify_age_very_recent():
    assert classify_age(0.5) == FreshnessTier.VERY_RECENT
    assert classify_age(2.0) == FreshnessTier.VERY_RECENT


def test_classify_age_recent():
    assert classify_age(5.0) == FreshnessTier.RECENT
    assert classify_age(14.0) == FreshnessTier.RECENT


def test_classify_age_older_evergreen():
    assert classify_age(15.0) == FreshnessTier.OLDER_EVERGREEN
    assert classify_age(400.0) == FreshnessTier.OLDER_EVERGREEN


def test_classify_age_unknown_for_missing_or_negative():
    assert classify_age(None) == FreshnessTier.UNKNOWN
    assert classify_age(-1.0) == FreshnessTier.UNKNOWN


def test_classify_published_at_missing_timestamp():
    tier, age_days = classify_published_at(None, as_of=AS_OF)
    assert tier == FreshnessTier.UNKNOWN
    assert age_days is None


def test_classify_published_at_malformed_timestamp():
    tier, age_days = classify_published_at("not-a-date", as_of=AS_OF)
    assert tier == FreshnessTier.UNKNOWN
    assert age_days is None


def test_classify_published_at_very_recent():
    published_at = (AS_OF - timedelta(hours=6)).isoformat()
    tier, age_days = classify_published_at(published_at, as_of=AS_OF)
    assert tier == FreshnessTier.VERY_RECENT
    assert 0 <= age_days < 1


def test_classify_published_at_recent():
    published_at = (AS_OF - timedelta(days=10)).isoformat()
    tier, age_days = classify_published_at(published_at, as_of=AS_OF)
    assert tier == FreshnessTier.RECENT
    assert 9 < age_days < 11


def test_classify_published_at_older_evergreen():
    published_at = (AS_OF - timedelta(days=200)).isoformat()
    tier, age_days = classify_published_at(published_at, as_of=AS_OF)
    assert tier == FreshnessTier.OLDER_EVERGREEN
    assert age_days > 100


def test_classify_published_at_handles_naive_datetime():
    naive = (AS_OF - timedelta(days=1)).replace(tzinfo=None).isoformat()
    tier, age_days = classify_published_at(naive, as_of=AS_OF)
    assert tier == FreshnessTier.VERY_RECENT
    assert age_days is not None


def test_freshness_score_is_within_scale_for_every_tier():
    for tier in FreshnessTier:
        score = freshness_score(tier)
        assert 0.0 <= score <= 10.0


def test_freshness_score_does_not_equal_popularity_by_construction():
    # Freshness tiers only ever compare against age thresholds -- there is no
    # popularity/demand input anywhere in this module.
    assert freshness_score(FreshnessTier.VERY_RECENT) >= freshness_score(FreshnessTier.OLDER_EVERGREEN)
