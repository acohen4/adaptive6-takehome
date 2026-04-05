from __future__ import annotations

from pathlib import Path

import pytest

from log_analytics.types import LogSummary

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SAMPLE_LINE = (
    '83.149.9.216 - - [17/May/2015:10:05:03 +0000] '
    '"GET /index.html HTTP/1.1" 200 1234 '
    '"http://example.com/" '
    '"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_1) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/32.0.1700.77 Safari/537.36"'
)

SAMPLE_LINE_LINUX_FIREFOX = (
    '93.114.45.13 - - [17/May/2015:10:05:14 +0000] '
    '"GET /articles/dynamic-dns-with-dhcp/ HTTP/1.1" 200 18848 '
    '"http://www.google.ro/" '
    '"Mozilla/5.0 (X11; Linux x86_64; rv:25.0) Gecko/20100101 Firefox/25.0"'
)

MALFORMED_LINE = "this is not a valid log line at all"

SAMPLE_LINE_NO_UA = (
    '200.49.190.101 - - [17/May/2015:10:05:36 +0000] '
    '"GET /reset.css HTTP/1.1" 200 1015 "-" "-"'
)


def _stub_country_lookup(ip: str) -> str:
    mapping = {
        "83.149.9.216": "Russia",
        "93.114.45.13": "Romania",
        "200.49.190.101": "Argentina",
        "10.0.0.1": "Unknown",
    }
    return mapping.get(ip, "Unknown")


@pytest.fixture()
def country_lookup():
    return _stub_country_lookup


@pytest.fixture()
def sample_summaries() -> list[LogSummary]:
    return [
        LogSummary(ip="1.1.1.1", os="Mac OS X", browser="Chrome", country="US"),
        LogSummary(ip="2.2.2.2", os="Linux", browser="Firefox", country="Germany"),
        LogSummary(ip="3.3.3.3", os="Mac OS X", browser="Chrome", country="US"),
        LogSummary(ip="4.4.4.4", os="Windows", browser="Edge", country="Germany"),
        LogSummary(ip="5.5.5.5", os="Linux", browser="Firefox", country="US"),
    ]


@pytest.fixture()
def apache_log_path() -> Path:
    return PROJECT_ROOT / "apache_log.txt"
