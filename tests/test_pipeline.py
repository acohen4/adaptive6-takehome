from __future__ import annotations

import io

from log_analytics.pipeline import (
    DEFAULT_EXTRACTORS,
    accumulate,
    format_report,
    read_lines,
)
from log_analytics.types import CategoryBreakdown, FullReport, LogSummary


class TestReadLines:
    def test_yields_non_empty_lines(self):
        source = io.StringIO("line one\n\n  line two  \n\n")
        result = list(read_lines(source))
        assert result == ["line one", "line two"]

    def test_empty_source(self):
        source = io.StringIO("")
        assert list(read_lines(source)) == []

    def test_strips_whitespace(self):
        source = io.StringIO("  hello  \n")
        assert list(read_lines(source)) == ["hello"]


class TestAccumulate:
    def test_counts_all_dimensions(self, sample_summaries):
        report = accumulate(iter(sample_summaries), DEFAULT_EXTRACTORS)

        assert report.errors == 0
        assert set(report.dimensions.keys()) == {"Country", "OS", "Browser"}

        country = report.dimensions["Country"]
        assert country.total == 5
        assert country.counts["US"] == 3
        assert country.counts["Germany"] == 2

        os_bd = report.dimensions["OS"]
        assert os_bd.counts["Mac OS X"] == 2
        assert os_bd.counts["Linux"] == 2
        assert os_bd.counts["Windows"] == 1

        browser = report.dimensions["Browser"]
        assert browser.counts["Chrome"] == 2
        assert browser.counts["Firefox"] == 2
        assert browser.counts["Edge"] == 1

    def test_counts_errors(self, sample_summaries):
        records = [sample_summaries[0], None, sample_summaries[1], None, None]
        report = accumulate(iter(records), DEFAULT_EXTRACTORS)

        assert report.errors == 3
        assert report.dimensions["Country"].total == 2

    def test_all_none(self):
        report = accumulate(iter([None, None]), DEFAULT_EXTRACTORS)
        assert report.errors == 2
        for bd in report.dimensions.values():
            assert bd.total == 0
            assert bd.counts == {}

    def test_empty_input(self):
        report = accumulate(iter([]), DEFAULT_EXTRACTORS)
        assert report.errors == 0
        for bd in report.dimensions.values():
            assert bd.total == 0

    def test_custom_extractor(self):
        records = [
            LogSummary(ip="1.1.1.1", os="Linux", browser="Chrome", country="US"),
            LogSummary(ip="2.2.2.2", os="Windows", browser="Firefox", country="UK"),
        ]
        extractors = {"OS": lambda s: s.os}
        report = accumulate(iter(records), extractors)

        assert set(report.dimensions.keys()) == {"OS"}
        assert report.dimensions["OS"].counts == {"Linux": 1, "Windows": 1}


class TestFormatReport:
    def test_contains_dimension_headers(self):
        report = FullReport(
            dimensions={
                "Browser": CategoryBreakdown(counts={"Chrome": 3, "Firefox": 1}, total=4),
            },
            errors=1,
        )
        text = format_report(report)

        assert "Browser:" in text
        assert "Chrome 75.00%" in text
        assert "Firefox 25.00%" in text

    def test_sorted_descending(self):
        report = FullReport(
            dimensions={
                "OS": CategoryBreakdown(counts={"A": 1, "B": 10, "C": 5}, total=16),
            },
            errors=0,
        )
        text = format_report(report)
        lines = [l.strip() for l in text.strip().splitlines() if "%" in l]
        assert lines[0].startswith("B ")
        assert lines[1].startswith("C ")
        assert lines[2].startswith("A ")

    def test_zero_total_no_division_error(self):
        report = FullReport(
            dimensions={"X": CategoryBreakdown(counts={}, total=0)},
            errors=5,
        )
        text = format_report(report)
        assert "X:" in text

    def test_multiple_dimensions(self, sample_summaries):
        report = accumulate(iter(sample_summaries), DEFAULT_EXTRACTORS)
        text = format_report(report)

        assert "Country:" in text
        assert "OS:" in text
        assert "Browser:" in text
