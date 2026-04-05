from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class LogSummary:
    ip: str
    os: str
    browser: str
    country: str


@dataclass
class CategoryBreakdown:
    counts: dict[str, int]
    total: int


@dataclass
class FullReport:
    dimensions: dict[str, CategoryBreakdown]
    errors: int


Extractor = Callable[[LogSummary], str]
ExtractorMap = dict[str, Extractor]

Formatter = Callable[["FullReport"], str]
