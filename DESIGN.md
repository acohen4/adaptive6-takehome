# Log Analytics System — Design Document

## 1. Problem Statement

Build a module that reads an Apache Web Server log file and outputs a statistical report showing what percentage of requests came from each dimension: **Country**, **OS**, and **Browser**.

## 2. Guiding Principles

- **Usability** — clean API surface; easy to call, easy to read output → [Public API](#32-public-api)
- **Simplicity** — a linear pipeline is the simplest model for single-pass analytics; O(1) memory is a bonus → [Data Flow Model](#41-data-flow-model)
- **Extensibility** — adding a new dimension should be a one-function change → [Extension Strategy](#43-extension-strategy-adding-new-dimensions)
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
CountryLookup = Callable[[str], str]

def analyze(
    source: IO[str],
    country_lookup: CountryLookup,
    dest: IO[str] = sys.stdout,
    extractors: ExtractorMap = DEFAULT_EXTRACTORS,
) -> FullReport:
```

Single entry point. `country_lookup` is a callback that maps an IP to a country name — the caller owns the GeoIP reader lifecycle (see [GeoIP Dependency Injection](#44-geoip-dependency-injection)).

### 3.3 Pipeline Stages

Each stage has a single responsibility and communicates through the types above.

```python
read_lines(source: IO[str]) -> Iterable[str]
parse_line(raw: str, country_lookup: CountryLookup) -> LogSummary | None
accumulate(records: Iterable[LogSummary | None], extractors: ExtractorMap) -> FullReport
format_report(report: FullReport) -> str
```

`parse_line` receives a `country_lookup` callback rather than owning a GeoIP reader. The caller (typically `analyze`, which gets it from `__main__`) controls reader creation and lifecycle. Tests pass a stub: `lambda ip: {"1.2.3.4": "Germany"}.get(ip, "Unknown")`.

`format_report` sorts each dimension's entries by count descending and formats percentages to two decimal places (`f"{pct:.2f}%"`), matching the required output spec.

### 3.4 Exported Surface

**Public:** `analyze`, `make_country_lookup`, all dataclasses/type aliases, `DEFAULT_EXTRACTORS`.

**Advanced:** `parse_line`, `accumulate`, `format_report` — for testing and custom pipelines.

## 4. Design Dimensions

### 4.1 Data Flow Model

**Decision:** Linear streaming pipeline, no shared mutable state.

```
                                    country_lookup
                                        │
┌────────────┐    ┌────────────┐    ┌───▼─────────┐    ┌───────────────┐    ┌──────┐
│ log file   │───▶│ read_lines │───▶│ parse_line  │───▶│  accumulate   │───▶│format│
│ (IO[str])  │    │ Iterable   │    │ LogSummary? │    │  FullReport   │    │report│
└────────────┘    │ [str]      │    │             │    │               │    └──┬───┘
                  └────────────┘    └─────────────┘    │ ExtractorMap  │       │
                                                       │ drives which  │       ▼
                                                       │ dims to count │    dest
                                                       └───────────────┘  (IO[str])
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
analyze(source, country_lookup, extractors={**DEFAULT_EXTRACTORS, "Method": lambda s: s.method})
```

If extractor composition becomes common, a `with_extractors(extras, base=DEFAULT_EXTRACTORS)` helper could make it more ergonomic — but `{**DEFAULT_EXTRACTORS, ...}` is clear enough for v1.

### 4.4 GeoIP Dependency Injection

**Decision:** `country_lookup` callback — `parse_line` and `analyze` receive a `Callable[[str], str]` instead of owning a GeoIP reader.

A `make_country_lookup(db_path)` factory opens the MaxMind DB and returns a closure. The CLI creates it; tests substitute a stub dict.

| Option | Pros | Cons |
|---|---|---|
| Global singleton + env var | Zero-arg `analyze()` calls | Import-order sensitive, hard to test in parallel, hidden state |
| `make_parser` factory (returns closure) | No global state, self-contained | Extra abstraction layer for one dependency |
| **`country_lookup` callback (chosen)** | Minimal API surface, tests already use it, caller owns lifecycle | GeoIP setup leaks to the call site |

The callback won over the factory because `parse_line` already needed a `country_lookup` parameter for testability — promoting it to `analyze`'s signature removes all global state with no new abstractions.

### 4.5 CLI Entry Point

A thin `__main__.py` wires up argument parsing and calls `analyze`:

```
python -m log_analytics apache_log.txt --db GeoLite2-Country.mmdb
```

The CLI parses args, opens files, creates the `country_lookup`, and delegates to `analyze`.

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
