from __future__ import annotations

import io
from pathlib import Path

import pytest

from log_analytics.parser import make_country_lookup
from log_analytics.pipeline import DEFAULT_EXTRACTORS, analyze
from log_analytics.types import LogSummary

from tests.conftest import _stub_country_lookup


class TestAnalyzeUnit:
    """Tests that exercise analyze() end-to-end with a stub country lookup."""

    def test_small_input(self, country_lookup):
        lines = (
            '83.149.9.216 - - [17/May/2015:10:05:03 +0000] '
            '"GET / HTTP/1.1" 200 1234 "-" '
            '"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_1) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/32.0.1700.77 Safari/537.36"\n'
            '93.114.45.13 - - [17/May/2015:10:05:14 +0000] '
            '"GET /page HTTP/1.1" 200 500 "-" '
            '"Mozilla/5.0 (X11; Linux x86_64; rv:25.0) Gecko/20100101 Firefox/25.0"\n'
            "bad line\n"
        )
        source = io.StringIO(lines)
        dest = io.StringIO()

        report = analyze(source, dest=dest, country_lookup=country_lookup)

        assert report.errors == 1
        assert report.dimensions["Country"].total == 2
        assert report.dimensions["OS"].total == 2
        assert report.dimensions["Browser"].total == 2

        output = dest.getvalue()
        assert "Country:" in output
        assert "OS:" in output
        assert "Browser:" in output

    def test_output_contains_percentages(self, country_lookup):
        line = (
            '83.149.9.216 - - [17/May/2015:10:05:03 +0000] '
            '"GET / HTTP/1.1" 200 1234 "-" '
            '"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_1) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/32.0.1700.77 Safari/537.36"\n'
        )
        dest = io.StringIO()
        analyze(io.StringIO(line), dest=dest, country_lookup=country_lookup)
        output = dest.getvalue()
        assert "100.00%" in output

    def test_asymmetric_percentages(self, country_lookup):
        lines = (
            '83.149.9.216 - - [17/May/2015:10:05:03 +0000] '
            '"GET / HTTP/1.1" 200 1234 "-" '
            '"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_1) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/32.0.1700.77 Safari/537.36"\n'
            '83.149.9.216 - - [17/May/2015:10:05:04 +0000] '
            '"GET /about HTTP/1.1" 200 800 "-" '
            '"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_1) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/32.0.1700.77 Safari/537.36"\n'
            '93.114.45.13 - - [17/May/2015:10:05:14 +0000] '
            '"GET /page HTTP/1.1" 200 500 "-" '
            '"Mozilla/5.0 (X11; Linux x86_64; rv:25.0) Gecko/20100101 Firefox/25.0"\n'
        )
        dest = io.StringIO()
        report = analyze(io.StringIO(lines), dest=dest, country_lookup=country_lookup)

        assert report.dimensions["Country"].counts["Russia"] == 2
        assert report.dimensions["Country"].counts["Romania"] == 1

        output = dest.getvalue()
        assert "66.67%" in output
        assert "33.33%" in output

    def test_all_malformed(self, country_lookup):
        source = io.StringIO("garbage\nmore garbage\n")
        dest = io.StringIO()
        report = analyze(source, dest=dest, country_lookup=country_lookup)
        assert report.errors == 2
        for bd in report.dimensions.values():
            assert bd.total == 0


@pytest.mark.integration
class TestAnalyzeIntegration:
    """Full integration test requiring the GeoLite2 DB and apache_log.txt."""

    def test_analyze_apache_log(self, apache_log_path: Path):
        if not apache_log_path.exists():
            pytest.skip("apache_log.txt not found")

        country_lookup = make_country_lookup()
        dest = io.StringIO()
        with open(apache_log_path, encoding="utf-8") as fh:
            report = analyze(fh, country_lookup=country_lookup, dest=dest)

        assert set(report.dimensions.keys()) == {"Country", "OS", "Browser"}
        for bd in report.dimensions.values():
            assert bd.total > 0

        total = report.dimensions["Country"].total
        error_ratio = report.errors / (total + report.errors) if (total + report.errors) else 0
        assert error_ratio < 0.5, f"Too many errors: {report.errors}/{total + report.errors}"

        output = dest.getvalue()
        assert "Country:" in output
        assert "OS:" in output
        assert "Browser:" in output
