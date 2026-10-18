"""Lifecycle and file configuration for a resumable single-worker crawl."""

import fcntl
import json
import logging
from contextlib import contextmanager
from dataclasses import asdict
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from crawler.discovery import discover_course_urls
from crawler.http import HttpClient, HttpPolicy
from crawler.pipeline import crawl_courses
from crawler.storage import CrawlStore


@contextmanager
def crawl_lock(database: Path):
    """Prevent two local workers from consuming the same checkpoint (POSIX)."""
    path = Path(str(database) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another crawler is using this database.") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def crawl_logging(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=10_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger = logging.getLogger("crawler")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield logger
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        handler.close()


def run_crawl(
    *,
    database: Path,
    output: Path,
    log_file: Path,
    policy: HttpPolicy = HttpPolicy(),
    url_file: Optional[Path] = None,
    limit: Optional[int] = None,
) -> dict:
    """Discover once, resume persisted tasks, and export committed results.

    An explicit URL file can add tasks without re-fetching the search catalog.
    Interruptions still export previously committed results before propagating.
    """
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer.")
    paths = [Path(path).resolve() for path in (database, output, log_file)]
    if url_file is not None:
        paths.append(Path(url_file).resolve())
    if len(set(paths)) != len(paths):
        raise ValueError("Database, output, log and input files must be different.")
    with crawl_lock(Path(database).resolve()), crawl_logging(Path(log_file)) as logger:
        try:
            with CrawlStore(database) as store, HttpClient(policy) as client:
                if url_file is not None:
                    urls = json.loads(Path(url_file).read_text(encoding="utf-8"))
                    if (
                        not isinstance(urls, list)
                        or not urls
                        or not all(isinstance(url, str) for url in urls)
                    ):
                        raise ValueError(
                            "URL file must contain a nonempty JSON array of URLs."
                        )
                    store.enqueue(urls)
                elif not sum(store.counts().values()):
                    logger.info("discovery_started")
                    store.enqueue(
                        discover_course_urls(client.get_text, delay_seconds=0)
                    )
                    logger.info(
                        "discovery_completed queued=%s", sum(store.counts().values())
                    )
                try:
                    summary = crawl_courses(store, client.get_text, limit=limit)
                finally:
                    snapshot = store.export(output)
                result = {
                    "run": asdict(summary),
                    "queue": store.counts(),
                    "snapshot": str(snapshot),
                }
                logger.info("crawl_completed summary=%s", json.dumps(result))
                return result
        except KeyboardInterrupt:
            logger.warning("crawl_interrupted; committed results remain resumable")
            raise
        except Exception:
            logger.exception("crawl_aborted")
            raise
