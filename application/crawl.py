"""Application service joining discovery, fetching, parsing and persistence."""

import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Optional

from application.models import CrawlSummary, PageOutcome
from application.policies import validate_worker_count
from application.ports import CourseParser, CourseStore, CourseValidator, PageFetcher
from domain.course import validate_course

LOGGER = logging.getLogger(__name__)


def _process_page(url, fetch_page, parse_course, validator, logger) -> PageOutcome:
    stage = "fetch"
    try:
        html = fetch_page(url)
        stage = "parse"
        result = validator(parse_course(html), expected_url=url)
        return PageOutcome(url, result=result)
    except Exception as error:
        logger.exception("course_failed url=%s stage=%s", url, stage)
        return PageOutcome(url, error=f"{stage}: {type(error).__name__}: {error}")


def _outcomes(urls, fetch_page, parse_course, workers, validator, logger):
    if workers == 1:
        for url in urls:
            yield _process_page(url, fetch_page, parse_course, validator, logger)
        return
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="course")
    pending = set()
    iterator = iter(urls)

    def submit_one():
        url = next(iterator, None)
        if url is not None:
            pending.add(
                executor.submit(
                    _process_page, url, fetch_page, parse_course, validator, logger
                )
            )

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
    store: CourseStore,
    fetch_page: PageFetcher,
    *,
    parse_course: CourseParser,
    validator: CourseValidator = validate_course,
    logger: Optional[logging.Logger] = None,
    limit: Optional[int] = None,
    retry_failed: bool = False,
    commit_batch_size: int = 1,
    workers: int = 1,
) -> CrawlSummary:
    """Process each unfinished URL at most once per run, isolating page failures.

    Disk/database errors and interrupts propagate: treating a failed checkpoint
    as a successful page would make recovery unreliable.
    """
    validate_worker_count(workers)
    log = logger if logger is not None else LOGGER
    succeeded = failed = 0
    with (
        store.work_queue(limit, retry_failed=retry_failed) as queue,
        store.batch_writes(commit_batch_size),
    ):
        outcomes = _outcomes(
            queue.urls, fetch_page, parse_course, workers, validator, log
        )
        try:
            for index, outcome in enumerate(outcomes, 1):
                if outcome.error is not None:
                    store.save_failure(outcome.url, outcome.error)
                    failed += 1
                else:
                    store.save_success(outcome.url, outcome.result)
                    succeeded += 1
                log.info(
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
