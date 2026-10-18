"""Application service joining discovery, fetching, parsing and persistence."""

import logging
from dataclasses import dataclass
from typing import Callable

from crawler.storage import CrawlStore
from crawler.utm_course import parse_course_page

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrawlSummary:
    processed: int
    succeeded: int
    failed: int


def crawl_courses(
    store: CrawlStore,
    fetch_page: Callable[[str], str],
    *,
    parse_course: Callable[[str], dict] = parse_course_page,
    limit=None,
    retry_failed: bool = False,
) -> CrawlSummary:
    """Process each unfinished URL at most once per run, isolating page failures.

    Disk/database errors and interrupts propagate: treating a failed checkpoint
    as a successful page would make recovery unreliable.
    """
    succeeded = failed = 0
    urls = store.work_urls(limit, retry_failed=retry_failed)
    for index, url in enumerate(urls, 1):
        stage = "fetch"
        try:
            html = fetch_page(url)
            stage = "parse"
            result = parse_course(html)
            if result.get("url") != url:
                raise ValueError("Parsed course URL does not match the queued URL.")
        except Exception as error:
            store.save_failure(url, f"{stage}: {type(error).__name__}: {error}")
            LOGGER.exception("course_failed url=%s stage=%s", url, stage)
            failed += 1
        else:
            store.save_success(url, result)
            succeeded += 1
        LOGGER.info(
            "crawl_progress processed=%s total=%s succeeded=%s failed=%s url=%s",
            index,
            len(urls),
            succeeded,
            failed,
            url,
        )
    return CrawlSummary(len(urls), succeeded, failed)
