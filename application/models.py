"""Value objects shared by use cases and their storage adapters."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Optional

from domain.course import Course


@dataclass(frozen=True)
class WorkQueue:
    total: int
    urls: Iterator[str]


@dataclass(frozen=True)
class CrawlSummary:
    processed: int
    succeeded: int
    failed: int


@dataclass(frozen=True)
class PageOutcome:
    url: str
    result: Optional[Course] = None
    error: Optional[str] = None
