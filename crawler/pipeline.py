"""UTM compatibility facade over the source-independent crawl use case."""

import logging
from typing import Callable

from application.crawl import crawl_courses as execute_crawl
from application.models import CrawlSummary as CrawlSummary
from application.models import PageOutcome as PageOutcome
from application.ports import CourseStore, PageFetcher
from crawler.utm_course import parse_course_document
from crawler.validation import validate_utm_course_model

LOGGER = logging.getLogger(__name__)


def crawl_courses(
    store: CourseStore,
    fetch_page: PageFetcher,
    *,
    parse_course: Callable[[str], object] = parse_course_document,
    limit=None,
    retry_failed: bool = False,
    commit_batch_size: int = 1,
    workers: int = 1,
) -> CrawlSummary:
    return execute_crawl(
        store,
        fetch_page,
        parse_course=parse_course,
        validator=validate_utm_course_model,
        logger=LOGGER,
        limit=limit,
        retry_failed=retry_failed,
        commit_batch_size=commit_batch_size,
        workers=workers,
    )
