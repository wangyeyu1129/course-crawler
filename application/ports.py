"""Narrow structural interfaces; adapters need no framework or base class."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Optional, Protocol

from application.models import WorkQueue
from domain.course import Course

CourseParser = Callable[[str], object]
PageFetcher = Callable[[str], str]


class CourseValidator(Protocol):
    def __call__(self, value: object, expected_url: Optional[str] = None) -> Course: ...


class CourseStore(Protocol):
    def work_queue(
        self, limit: Optional[int] = None, *, retry_failed: bool = False
    ) -> AbstractContextManager[WorkQueue]: ...

    def batch_writes(self, size: int = 1) -> AbstractContextManager[None]: ...

    def save_success(self, url: str, course: Course, /) -> None: ...

    def save_failure(self, url: str, error: str, /) -> None: ...
