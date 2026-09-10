import json
import socket
import ssl
import urllib.error
import xml.etree.ElementTree as ET

from scripts.research.models import SourceHealth
from scripts.research.source_health import classify_error, is_degraded


def test_classify_error_timeout():
    assert classify_error(TimeoutError("timed out")) == "timeout"
    assert classify_error(socket.timeout("timed out")) == "timeout"


def test_classify_error_network():
    assert classify_error(urllib.error.URLError("connection refused")) == "network"


def test_classify_error_tls():
    exc = urllib.error.URLError(ssl.SSLError("certificate verify failed"))
    assert classify_error(exc) == "tls_error"


def test_classify_error_parse_error_for_malformed_xml():
    try:
        ET.fromstring(b"<not><valid</xml>")
    except ET.ParseError as exc:
        assert classify_error(exc) == "parse_error"
    else:
        raise AssertionError("expected ET.ParseError")


def test_classify_error_parse_error_for_malformed_json():
    try:
        json.loads("{not valid json")
    except json.JSONDecodeError as exc:
        assert classify_error(exc) == "parse_error"
    else:
        raise AssertionError("expected json.JSONDecodeError")


def test_classify_error_config_error():
    assert classify_error(FileNotFoundError("missing.json")) == "config_error"


def test_classify_error_unknown_fallback():
    assert classify_error(ValueError("something else")) == "unknown"


def _health(name: str, success: bool) -> SourceHealth:
    return SourceHealth(
        source_name=name,
        success=success,
        item_count=1 if success else 0,
        duration_seconds=0.1,
        retrieved_at="2026-01-01T00:00:00+00:00",
    )


def test_is_degraded_when_no_sources_ran():
    assert is_degraded([]) is True


def test_is_degraded_when_half_or_more_sources_fail():
    reports = [_health("a", True), _health("b", False)]
    assert is_degraded(reports) is True


def test_not_degraded_when_most_sources_succeed():
    reports = [_health("a", True), _health("b", True), _health("c", False)]
    assert is_degraded(reports) is False
