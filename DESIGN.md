# Log Analytics System — Design Document

## 1. Problem Statement

Build a module that reads an Apache Web Server log file and outputs a statistical report showing what percentage of requests came from each dimension: **Country**, **OS**, and **Browser**.

## 2. Guiding Principles

- **Usability** — clean API surface; easy to call, easy to read output → [Public API](#32-public-api)
- **Simplicity** — a linear pipeline is the simplest model for single-pass analytics; O(1) memory is a bonus → [Data Flow Model](#41-data-flow-model)
- **Extensibility** — adding a new dimension or output format should be a one-function change → [Extension Strategy](#43-extension-strategy-adding-new-dimensions), [Output Formats](#44-output-format-extensibility)
- **Reliability** — errors are handled explicitly and surfaced, never silently swallowed → [Error Boundary Placement](#42-error-boundary-placement)

## 3. Interfaces & Data Structures

### 3.1 Core Types

```python
from dataclasses import dataclass
from typing import Callable, IO, Iterable

@dataclass
class LogSummary:
    ip: str
    os: str
    browser: str
    country: str
```

`LogSummary` intentionally conflates raw parsing (extracting the IP and user-agent string) with enrichment (resolving IP → country, user-agent → OS/browser). A purer design would separate these into a `RawLogEntry` and an enrichment step, but for three dimensions the extra indirection isn't worth it. If enrichment logic grows (e.g. ASN lookup, bot detection), splitting the stages would be the natural next refactor.

```python
@dataclass
class CategoryBreakdown:
    counts: dict[str, int]   # e.g. {"Chrome": 412, "Firefox": 87}
    total: int

@dataclass
class FullReport:
    dimensions: dict[str, CategoryBreakdown]  # keyed by extractor name
    errors: int

Extractor = Callable[[LogSummary], str]
# e.g. {"OS": lambda s: s.os, "Browser": lambda s: s.browser}
ExtractorMap = dict[str, Extractor]
```

### 3.2 Public API

```python
Formatter = Callable[[FullReport], str]

def analyze(
    source: IO[str],
    geoip_db: str,
    dest: IO[str] = sys.stdout,
    extractors: ExtractorMap = DEFAULT_EXTRACTORS,
    formatter: Formatter = format_report,
) -> FullReport:
```

Single entry point. `source`/`dest` are streams (tests pass `StringIO`); `geoip_db` is a path because the MaxMind reader manages its own file handle. `formatter` can be swapped (see [Output Format Extensibility](#44-output-format-extensibility)).

### 3.3 Pipeline Stages

Each stage has a single responsibility and communicates through the types above.

```python
read_lines(source: IO[str]) -> Iterable[str]
make_parser(geoip_db: str) -> Callable[[str], LogSummary | None]
accumulate(records: Iterable[LogSummary | None], extractors: ExtractorMap) -> FullReport
format_report(report: FullReport) -> str
```

`make_parser` is a factory: it opens the MaxMind DB and creates the UA parser once, then returns a `parse_line` closure that captures both. Tests can swap in a mock reader without global state.

```python
def make_parser(geoip_db: str) -> Callable[[str], LogSummary | None]:
    reader = geoip2.database.Reader(geoip_db)
    ua = ua_parser.Parser()   # stateless, no config needed
    def parse_line(raw: str) -> LogSummary | None:
        ...  # uses reader and ua via closure
    return parse_line
```

`format_report` sorts each dimension's entries by count descending and formats percentages to two decimal places (`f"{pct:.2f}%"`), matching the required output spec.

### 3.4 Exported Surface

**Public:** `analyze`, all dataclasses/type aliases, `DEFAULT_EXTRACTORS`.

**Advanced:** `make_parser`, `accumulate`, `format_report` — for testing and custom pipelines. `read_lines` is internal.

## 4. Design Dimensions

### 4.1 Data Flow Model

**Decision:** Linear streaming pipeline, no shared mutable state.

```
┌────────────┐    ┌────────────┐    ┌─────────────┐    ┌───────────────┐    ┌──────┐
│ log file   │───▶│ read_lines │───▶│ parse_line  │───▶│  accumulate   │───▶│format│
│ (IO[str])  │    │ Iterable   │    │ LogSummary? │    │  FullReport   │    │report│
└────────────┘    │ [str]      │    │             │    │               │    └──┬───┘
                  └────────────┘    │ ┌─────────┐ │    │ ExtractorMap  │       │
                                    │ │GeoIP DB │ │    │ drives which  │       ▼
                                    │ │UA parser│ │    │ dims to count │    dest
                                    │ └─────────┘ │    └───────────────┘  (IO[str])
                                    └─────────────┘
```

| Option | Pros | Cons |
|---|---|---|
| **Streaming pipeline (chosen)** | O(1) memory, testable pure stages, parallelizable | Harder to do multi-pass analytics |
| Load-all-in-memory | Simple, random access | Memory-bound on large files |
| Event-driven / callback | Flexible composition | Harder to reason about and test |

Each stage consumes an iterable and produces a typed output. Testability comes from pure functions, memory efficiency from lazy iteration. Multi-pass analytics aren't needed for percentage breakdowns, so streaming is a clear win.

### 4.2 Error Boundary Placement

**Decision:** Errors concentrate at `parse_line`; bad lines become `None` + an increment to the error count.

| Option | Pros | Cons |
|---|---|---|
| Fail-fast (raise on bad line) | Simple, no ambiguity | One bad line kills the whole report |
| **Skip + count (chosen)** | Resilient, observable via `errors` count | Data loss if threshold not monitored |
| Quarantine file | Full auditability, replay | More machinery than this problem needs |

This gives two clean failure modes: data-quality issues (tracked, non-fatal) and IO failures (exceptional, propagated). Downstream stages can assume clean data.

### 4.3 Extension Strategy (Adding New Dimensions)

**Decision:** `ExtractorMap` drives both accumulation and report shape. `FullReport.dimensions` is a `dict[str, CategoryBreakdown]` keyed by the same strings as the extractor map, so adding a dimension means adding one entry to the extractor map — no core type changes.

| Option | Pros | Cons |
|---|---|---|
| Named fields + extractors | Type-safe, discoverable, IDE-friendly | Must touch `FullReport` to add a dimension |
| **`dict[str, CategoryBreakdown]` (chosen)** | Unlimited dimensions, no core changes, matches `ExtractorMap` 1:1 | No static field-level type safety; keys are strings |
| Plugin/registry pattern | Fully decoupled | Way more machinery than three dimensions need |

String keys are an acceptable trade-off: the extractor map is the single source of truth for which dimensions exist, and the report's keys are always produced by the pipeline — never hand-typed by callers.

**Extensibility boundary:** `LogSummary` has fixed fields, so `parse_line` needs a code change when a new dimension arrives. Everything downstream (`accumulate`, `format_report`, `FullReport`) is dimension-agnostic and never changes.

Adding a "Method" dimension requires three localized edits, zero pipeline changes:

```python
# 1. Add field to LogSummary
# 2. Populate it in parse_line
# 3. Register the extractor:
analyze(source, geoip_db, extractors={**DEFAULT_EXTRACTORS, "Method": lambda s: s.method})
```

If extractor composition becomes common, a `with_extractors(extras, base=DEFAULT_EXTRACTORS)` helper could make it more ergonomic — but `{**DEFAULT_EXTRACTORS, ...}` is clear enough for v1.

### 4.4 Output Format Extensibility

**Decision:** `Formatter = Callable[[FullReport], str]` — same pattern as `ExtractorMap`.

The default `format_report` produces the required plain-text output (sorted descending, two decimal places). Alternative formatters can be passed to `analyze` or selected via CLI flag:

```python
def format_json(report: FullReport) -> str: ...
def format_csv(report: FullReport) -> str: ...
```

| Option | Pros | Cons |
|---|---|---|
| **Callable formatter (chosen)** | Trivial to add, matches extractor pattern | No formal interface to enforce |
| Template engine (Jinja2) | Flexible layouts | Heavy for tabular stats |
| Subclass / strategy object | Formal interface | Overkill for `FullReport -> str` |

Adding a new output format means writing one function with signature `FullReport -> str` — no pipeline changes.

### 4.5 CLI Entry Point

A thin `__main__.py` wires up argument parsing and calls `analyze`:

```
python -m log_analyzer apache_log.txt --geoip-db GeoLite2-Country.mmdb [--format json]
```

The CLI parses args, opens files, picks a formatter, and delegates to `analyze`.

### 4.6 Parsing Strategy

**Decision:** `apache-log-parser` — a log parser exists, so we use it.

| Option | Pros | Cons |
|---|---|---|
| Regex | Precise, handles edge cases | Harder to read and maintain |
| `str.split` | Simple | Fragile with quoted strings, spaces in user-agent |
| **`apache-log-parser` (chosen)** | Battle-tested, handles format variants | External dependency |

### 4.7 User-Agent Resolution

**Decision:** `ua-parser` — best coverage of the lightweight options.

| Option | Pros | Cons |
|---|---|---|
| `user-agents` | Lightweight, simple API | May lack coverage for edge cases |
| **`ua-parser` (chosen)** | Comprehensive, well-maintained regex DB | Heavier dependency |
| Manual regex | No dependencies | Maintenance burden, poor coverage |

### 4.8 GeoIP Lookup

**Decision:** `geoip2` + MaxMind DB — offline, fast, industry standard.

| Option | Pros | Cons |
|---|---|---|
| **`geoip2` + MaxMind DB (chosen)** | Industry standard, offline, fast | Requires DB file download |
| IP-to-country API | No local DB needed | Network dependency, rate limits, latency |
| Bundled CSV lookup | Simple, no external deps | Stale data, poor coverage |

## 5. Assumptions & Open Questions

**Assumptions:**
- Single-pass aggregation (counts + percentages) is sufficient. Multi-pass analytics (medians, correlations, dependent sub-breakdowns) are out of scope.

**Open questions:**
- [ ] Error threshold that aborts the report if too many lines are malformed?
- [ ] Do we constrain allowed values per dimension (e.g. known OS list), or accept whatever the parser returns?
