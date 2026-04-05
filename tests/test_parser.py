from __future__ import annotations

from tests.conftest import (
    MALFORMED_LINE,
    SAMPLE_LINE,
    SAMPLE_LINE_LINUX_FIREFOX,
    SAMPLE_LINE_NO_UA,
)

from log_analytics.parser import parse_line


class TestParseLine:
    def test_parse_valid_chrome_line(self, country_lookup):
        result = parse_line(SAMPLE_LINE, country_lookup=country_lookup)

        assert result is not None
        assert result.ip == "83.149.9.216"
        assert result.browser == "Chrome"
        assert result.os == "Mac OS X"
        assert result.country == "Russia"

    def test_parse_valid_firefox_line(self, country_lookup):
        result = parse_line(SAMPLE_LINE_LINUX_FIREFOX, country_lookup=country_lookup)

        assert result is not None
        assert result.ip == "93.114.45.13"
        assert result.browser == "Firefox"
        assert result.os == "Linux"
        assert result.country == "Romania"

    def test_parse_malformed_line_returns_none(self, country_lookup):
        result = parse_line(MALFORMED_LINE, country_lookup=country_lookup)
        assert result is None

    def test_parse_empty_string_returns_none(self, country_lookup):
        result = parse_line("", country_lookup=country_lookup)
        assert result is None

    def test_parse_missing_user_agent(self, country_lookup):
        result = parse_line(SAMPLE_LINE_NO_UA, country_lookup=country_lookup)

        assert result is not None
        assert result.ip == "200.49.190.101"
        assert result.country == "Argentina"
        # "-" UA should produce "Other" for both
        assert result.browser == "Other"
        assert result.os == "Other"

    def test_parse_geoip_failure_returns_none(self):
        def failing_lookup(ip: str) -> str:
            raise RuntimeError("GeoIP DB unavailable")

        result = parse_line(SAMPLE_LINE, country_lookup=failing_lookup)
        assert result is None

    def test_parse_preserves_ip(self, country_lookup):
        result = parse_line(SAMPLE_LINE, country_lookup=country_lookup)
        assert result is not None
        assert result.ip == "83.149.9.216"
