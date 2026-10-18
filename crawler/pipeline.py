"""Application service joining discovery, fetching, parsing and persistence."""

import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Optional

from crawler.models import Course, validate_course
from crawler.storage import CrawlStore
from crawler.utm_course import parse_course_page

LOGGER = logging.getLogger(__name__)


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


def _process_page(url, fetch_page, parse_course) -> PageOutcome:
    stage = "fetch"
    try:
        html = fetch_page(url)
        stage = "parse"
        result = validate_course(parse_course(html), expected_url=url)
        return PageOutcome(url, result=result)
    except Exception as error:
        LOGGER.exception("course_failed url=%s stage=%s", url, stage)
        return PageOutcome(url, error=f"{stage}: {type(error).__name__}: {error}")


def _outcomes(urls, fetch_page, parse_course, workers):
    if workers == 1:
        for url in urls:
            yield _process_page(url, fetch_page, parse_course)
        return
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="course")
    pending = set()
    iterator = iter(urls)

    def submit_one():
        url = next(iterator, None)
        if url is not None:
            pending.add(executor.submit(_process_page, url, fetch_page, parse_course))

    try:
        for _ in range(workers):
            submit_one()
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
            for _ in done:
                submit_one()
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


def crawl_courses(
    store: CrawlStore,
    fetch_page: Callable[[str], str],
    *,
    parse_course: Callable[[str], dict] = parse_course_page,
    limit=None,
    retry_failed: bool = False,
    commit_batch_size: int = 1,
    workers: int = 1,
) -> CrawlSummary:
    """Process each unfinished URL at most once per run, isolating page failures.

    Disk/database errors and interrupts propagate: treating a failed checkpoint
    as a successful page would make recovery unreliable.
    """
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16.")
    succeeded = failed = 0
    with (
        store.work_queue(limit, retry_failed=retry_failed) as queue,
        store.batch_writes(commit_batch_size),
    ):
        outcomes = _outcomes(queue.urls, fetch_page, parse_course, workers)
        try:
            for index, outcome in enumerate(outcomes, 1):
                if outcome.error is not None:
                    store.save_failure(outcome.url, outcome.error)
                    failed += 1
                else:
                    store.save_success(outcome.url, outcome.result)
                    succeeded += 1
                LOGGER.info(
                    "crawl_progress processed=%s total=%s succeeded=%s failed=%s url=%s",
                    index,
                    queue.total,
                    succeeded,
                    failed,
                    outcome.url,
                )
        finally:
            outcomes.close()
    return CrawlSummary(queue.total, succeeded, failed)
