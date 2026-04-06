from __future__ import annotations

import sys
from collections import Counter
from typing import IO, Iterable

from .parser import parse_line, CountryLookup
from .types import (
    CategoryBreakdown,
    ExtractorMap,
    Formatter,
    FullReport,
    LogSummary,
)

DEFAULT_EXTRACTORS: ExtractorMap = {
    "Country": lambda s: s.country,
    "OS": lambda s: s.os,
    "Browser": lambda s: s.browser,
}


def read_lines(source: IO[str]) -> Iterable[str]:
    for line in source:
        stripped = line.strip()
        if stripped:
            yield stripped


def accumulate(
    records: Iterable[LogSummary | None],
    extractors: ExtractorMap,
) -> FullReport:
    counters: dict[str, Counter[str]] = {name: Counter() for name in extractors}
    errors = 0

    for record in records:
        if record is None:
            errors += 1
            continue
        for name, extract in extractors.items():
            counters[name][extract(record)] += 1

    dimensions: dict[str, CategoryBreakdown] = {}
    for name, counter in counters.items():
        total = sum(counter.values())
        dimensions[name] = CategoryBreakdown(counts=dict(counter), total=total)

    return FullReport(dimensions=dimensions, errors=errors)


def truncate_report(report: FullReport, top_n: int) -> FullReport:
    """Keep only the *top_n* entries per dimension, rolling the rest into "Other"."""
    truncated: dict[str, CategoryBreakdown] = {}
    for name, breakdown in report.dimensions.items():
        sorted_items = sorted(
            breakdown.counts.items(), key=lambda kv: kv[1], reverse=True
        )
        top = dict(sorted_items[:top_n])
        rest_total = sum(count for _, count in sorted_items[top_n:])
        if rest_total > 0:
            top["Other"] = top.get("Other", 0) + rest_total
        truncated[name] = CategoryBreakdown(counts=top, total=breakdown.total)
    return FullReport(dimensions=truncated, errors=report.errors)


def format_report(report: FullReport) -> str:
    sections: list[str] = []

    for dim_name, breakdown in report.dimensions.items():
        lines = [f"{dim_name}:"]
        sorted_items = sorted(
            breakdown.counts.items(), key=lambda kv: kv[1], reverse=True
        )
        for label, count in sorted_items:
            pct = (count / breakdown.total * 100) if breakdown.total else 0.0
            lines.append(f"{label} {pct:.2f}%")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def analyze(
    source: IO[str],
    country_lookup: CountryLookup,
    dest: IO[str] = sys.stdout,
    extractors: ExtractorMap = DEFAULT_EXTRACTORS,
    formatter: Formatter = format_report,
    top_n: int | None = None,
    verbose: bool = False,
) -> FullReport:
    records = (parse_line(line, country_lookup=country_lookup, verbose=verbose) for line in read_lines(source))
    report = accumulate(records, extractors)
    if top_n is not None:
        report = truncate_report(report, top_n)
    dest.write(formatter(report))
    dest.write("\n")
    return report
