# Log Analytics System — Design Document

## 1. Problem Statement

Build a module that reads an Apache Web Server log file and outputs a statistical report showing what percentage of requests came from each dimension: **Country**, **OS**, and **Browser**.

## 2. Guiding Principles

- **Usability** — clean API surface; easy to call, easy to read output → [Public API](#32-public-api)
- **Simplicity** — a linear pipeline is the simplest model for single-pass analytics; O(1) memory is a bonus → [Data Flow Model](#41-data-flow-model)
- **Extensibility** — adding a new dimension or output format should be a one-function change → [Extension Strategy](#43-extension-strategy-adding-new-dimensions), [Formatter Extensibility](#44-formatter-extensibility)
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

`LogSummary` intentionally conflates raw parsing (extracting the IP and user-agent string) with enrichment (resolving IP → country, user-agent → OS/browser). A purer design would separate these into a `RawLogEntry` and an enrichment step, but for three dimensions the extra indirection isn't worth it. If enrichment logic grows, splitting the stages would be the natural next refactor.

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

Formatter = Callable[[FullReport], str]
```

### 3.2 Public API

```python
CountryLookup = Callable[[str], str]

def analyze(
    source: IO[str],
    country_lookup: CountryLookup,
    dest: IO[str] = sys.stdout,
    extractors: ExtractorMap = DEFAULT_EXTRACTORS,
    formatter: Formatter = format_report,
    top_n: int | None = None,
) -> FullReport:
```

Single entry point. `country_lookup` is a callback that maps an IP to a country name — the caller owns the GeoIP reader lifecycle (see [GeoIP Dependency Injection](#45-geoip-dependency-injection)). `formatter` controls output serialisation; the default produces the human-readable text format (see [Formatter Extensibility](#44-formatter-extensibility)). `top_n`, when set, keeps only the N highest-count entries per dimension and rolls the remainder into an "Other" bucket (see [Top-N Truncation](#46-top-n-truncation)).

### 3.3 Pipeline Stages

Each stage has a single responsibility and communicates through the types above.

```python
read_lines(source: IO[str]) -> Iterable[str]
parse_line(raw: str, country_lookup: CountryLookup) -> LogSummary | None
accumulate(records: Iterable[LogSummary | None], extractors: ExtractorMap) -> FullReport
truncate_report(report: FullReport, top_n: int) -> FullReport
format_report(report: FullReport) -> str
```

### 3.4 Exported Surface

**Public:** `analyze`, `make_country_lookup`, all dataclasses/type aliases, `DEFAULT_EXTRACTORS`, `Formatter`.

**Advanced:** `parse_line`, `accumulate`, `truncate_report`, `format_report` (the default `Formatter`) — for testing and custom pipelines.

## 4. Design Dimensions

### 4.1 Data Flow Model

**Decision:** Linear streaming pipeline, no shared mutable state.

```
                                    country_lookup
                                        │
┌────────────┐    ┌────────────┐    ┌───▼─────────┐    ┌───────────────┐    ┌──────────┐    ┌──────┐
│ log file   │───▶│ read_lines │───▶│ parse_line  │───▶│  accumulate   │───▶│ truncate │───▶│format│
│ (IO[str])  │    │ Iterable   │    │ LogSummary? │    │  FullReport   │    │ (opt.)   │    │report│
└────────────┘    │ [str]      │    │             │    │               │    └──────────┘    └──┬───┘
                  └────────────┘    └─────────────┘    │ ExtractorMap  │                       │
                                                       │ drives which  │                       ▼
                                                       │ dims to count │                    dest
                                                       └───────────────┘                  (IO[str])
```

| Option                          | Pros                                              | Cons                              |
| ------------------------------- | ------------------------------------------------- | --------------------------------- |
| **Streaming pipeline (chosen)** | O(1) memory, testable pure stages, parallelizable | Harder to do multi-pass analytics |
| Load-all-in-memory              | Simple, random access                             | Memory-bound on large files       |
| Event-driven / callback         | Flexible composition                              | Harder to reason about and test   |

Each stage consumes an iterable and produces a typed output. Testability comes from pure functions, memory efficiency from lazy iteration. Multi-pass analytics aren't needed for percentage breakdowns, so streaming is a clear win.

### 4.2 Error Boundary Placement

**Decision:** Errors concentrate at `parse_line`; bad lines become `None` + an increment to the error count. Since this is a log statistical tool, we assume the data isn't totally clean and therefore shouldn't fail on bad log entries.

| Option                        | Pros                                     | Cons                                   |
| ----------------------------- | ---------------------------------------- | -------------------------------------- |
| Fail-fast (raise on bad line) | Simple, no ambiguity                     | One bad line kills the whole report    |
| **Skip + count (chosen)**     | Resilient, observable via `errors` count | Data loss if threshold not monitored   |
| Quarantine file               | Full auditability, replay                | More machinery than this problem needs |

This gives two clean failure modes: data-quality issues (tracked, non-fatal) and IO failures (exceptional, propagated). Downstream stages can assume clean data.

### 4.3 Extension Strategy (Adding New Dimensions)

**Decision:** `ExtractorMap` drives both accumulation and report shape. `FullReport.dimensions` is a `dict[str, CategoryBreakdown]` keyed by the same strings as the extractor map, so adding a dimension means adding one entry to the extractor map, without needing to make any core type changes.

| Option                                      | Pros                                                              | Cons                                                |
| ------------------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------- |
| Named fields + extractors                   | Type-safe, discoverable, IDE-friendly                             | Must touch `FullReport` to add a dimension          |
| **`dict[str, CategoryBreakdown]` (chosen)** | Unlimited dimensions, no core changes, matches `ExtractorMap` 1:1 | No static field-level type safety; keys are strings |
| Plugin/registry pattern                     | Fully decoupled                                                   | Way more machinery than three dimensions need       |

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

### 4.4 Formatter Extensibility

**Decision:** `Formatter` callback mirrors the `ExtractorMap` pattern on the output side. `analyze` accepts a `formatter: Formatter = format_report` parameter, so swapping output format is a one-function change.

| Option                             | Pros                                                                      | Cons                                                 |
| ---------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------- |
| Hardcoded text output (status quo) | Simple, no extra parameter                                                | Every new format means forking or wrapping `analyze` |
| **`Formatter` callback (chosen)**  | One-function swap, mirrors `ExtractorMap` symmetry, zero pipeline changes | Slight API surface growth                            |

The `Formatter` only consumes a `FullReport`, so it is fully decoupled from parsing and accumulation. This symmetry keeps the pipeline a clean sequence of pluggable stages.

Adding a JSON output format requires one function, zero pipeline changes:

```python
import json

def json_formatter(report: FullReport) -> str:
    return json.dumps({
        dim: {
            label: round(count / bd.total * 100, 2)
            for label, count in bd.counts.items()
        }
        for dim, bd in report.dimensions.items()
    }, indent=2)

analyze(source, country_lookup, formatter=json_formatter)
```

### 4.5 GeoIP Dependency Injection

**Decision:** `country_lookup` callback — `parse_line` and `analyze` receive a `Callable[[str], str]` instead of owning a GeoIP reader.

A `make_country_lookup(db_path)` factory opens the MaxMind DB and returns a closure. The CLI creates it; tests substitute a stub dict.

| Option                                  | Pros                                                             | Cons                                                           |
| --------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------- |
| Global singleton + env var              | Zero-arg `analyze()` calls                                       | Import-order sensitive, hard to test in parallel, hidden state |
| `make_parser` factory (returns closure) | No global state, self-contained                                  | Extra abstraction layer for one dependency                     |
| **`country_lookup` callback (chosen)**  | Minimal API surface, tests already use it, caller owns lifecycle | GeoIP setup leaks to the call site                             |

The callback won over the factory because `parse_line` already needed a `country_lookup` parameter for testability. Promoting it to `analyze`'s signature removes all global state with no new abstractions.

### 4.6 CLI Entry Point

A thin `__main__.py` wires up argument parsing and calls `analyze`:

```
python -m log_analytics apache_log.txt --db GeoLite2-Country.mmdb
```

The CLI parses args, opens files, creates the `country_lookup`, and delegates to `analyze`. The optional `--top-n N` flag is forwarded to `analyze` as `top_n`.

### 4.7 Top-N Truncation

**Decision:** A pure post-processing function `truncate_report` sits between `accumulate` and the formatter. When the caller passes `top_n`, `analyze` applies this step; otherwise the pipeline is unchanged.

| Option                         | Pros                                                          | Cons                                                        |
| ------------------------------ | ------------------------------------------------------------- | ----------------------------------------------------------- |
| **Post-process step (chosen)** | Works with any formatter, testable in isolation, non-invasive | The returned `FullReport` is lossy (tail entries collapsed) |
| Inside the formatter           | `FullReport` stays complete                                   | Every custom formatter must re-implement truncation         |
| Inside `accumulate`            | Single pass                                                   | Can't know the top-N until all records are counted          |

The function keeps `CategoryBreakdown.total` unchanged so percentage calculations remain correct. If a dimension already contains a natural "Other" label (e.g. from unresolvable user-agents), its count is merged with the rollup to avoid a duplicate bucket.

### 4.8 Library Choices

Each concern maps to a well-established library; in every case the main alternative was hand-rolling with regex.

| Concern               | Chosen library        | Why                                     | Main alternative trade-off                                             |
| --------------------- | --------------------- | --------------------------------------- | ---------------------------------------------------------------------- |
| Log parsing           | `apache-log-parser`   | Battle-tested, handles format variants  | Regex: precise but harder to read and maintain                         |
| User-agent resolution | `ua-parser`           | Comprehensive, well-maintained regex DB | `user-agents`: lighter but weaker edge-case coverage                   |
| GeoIP lookup          | `geoip2` + MaxMind DB | Industry standard, offline, fast        | IP-to-country API: no local DB but adds network dependency and latency |

### 4.9 Scaling Considerations

A potential bottleneck in throughput is input size. If the workload grows, we could increase throughput by parallelizing processing.

**Upstream splitting (easiest win).** Before touching pipeline code, shard at the infrastructure level: split logs and run independent `analyze` calls per shard. Merge the resulting `FullReport`s with element-wise addition of `CategoryBreakdown.counts`. Zero code change to the pipeline itself.

**In-process parallelism.** Because `accumulate` just increments `dict[str, int]` counters, the work is trivially partitionable: split input lines across workers, accumulate per chunk, then merge. A `merge_reports(list[FullReport]) -> FullReport` function is the only new code required.

For the current scope (single file, CLI tool) none of this is needed, but the streaming + pure-function architecture keeps the scaling path open without a rewrite.

## 5. Assumptions & Open Questions

**Assumptions:**

- Single-pass aggregation (counts + percentages) is sufficient. Multi-pass analytics (medians, correlations, dependent sub-breakdowns) are out of scope.
