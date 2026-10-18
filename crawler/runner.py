"""Lifecycle and file configuration for a resumable single-worker crawl."""

import fcntl
import json
import logging
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from crawler.discovery import (
    discover_course_urls,
    load_baseline_count,
    validate_count_drop,
)
from crawler.http import HttpClient, HttpPolicy
from crawler.http_cache import HttpCache
from crawler.pipeline import CrawlSummary, crawl_courses
from crawler.raw_data import RawArchive
from crawler.run_records import RunRecord
from crawler.storage import CrawlStore


@contextmanager
def crawl_lock(database: Path, resource: str = "database"):
    """Prevent two local workers from consuming the same checkpoint (POSIX)."""
    path = Path(str(database) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Another crawler is using this {resource}.") from error
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


def export_run(store: CrawlStore, output: Path, record: RunRecord) -> Path:
    counts = store.counts()
    complete = (
        bool(sum(counts.values())) and counts["pending"] == 0 and counts["failed"] == 0
    )
    target = (
        Path(output)
        if complete
        else Path(output).parent / "progress" / Path(output).name
    )
    snapshot = store.export(target)
    record.snapshot(snapshot)
    record.data["published"] = complete
    record.data["export_path"] = str(target.resolve())
    return snapshot


def run_crawl(
    *,
    database: Path,
    output: Path,
    log_file: Path,
    policy: HttpPolicy = HttpPolicy(),
    url_file: Optional[Path] = None,
    limit: Optional[int] = None,
    max_count_drop: float = 0.1,
    retry_failed: bool = False,
    commit_batch_size: int = 1,
    workers: int = 1,
    refresh: bool = False,
    http_cache: Optional[Path] = None,
    operation: str = "resume",
    raw_directory: Optional[Path] = None,
) -> dict:
    """Discover once, resume persisted tasks, and export committed results.

    An explicit URL file can add tasks without re-fetching the search catalog.
    Interruptions still export previously committed results before propagating.
    """
    if operation not in {"resume", "discover", "refresh", "reparse"}:
        raise ValueError("Unknown crawl operation.")
    if refresh:
        if operation != "resume":
            raise ValueError("refresh cannot be combined with an explicit operation.")
        operation = "refresh"
    if operation == "refresh" and url_file is not None:
        raise ValueError(
            "Refresh uses the known queue; import URLs with resume/discover first."
        )
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer.")
    if type(commit_batch_size) is not int or commit_batch_size < 1:
        raise ValueError("commit_batch_size must be a positive integer.")
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16.")
    validate_count_drop(max_count_drop)
    cache_path = (
        Path(http_cache)
        if http_cache is not None
        else Path(database).with_name(Path(database).stem + "_http.sqlite3")
    )
    raw_path = (
        Path(raw_directory)
        if raw_directory is not None
        else Path(output).parent / "raw" / Path(output).stem
    )
    paths = [raw_path.resolve(), Path(cache_path).resolve()] + [
        Path(path).resolve() for path in (database, output, log_file)
    ]
    progress_path = Path(output).parent / "progress" / Path(output).name
    paths.append(progress_path.resolve())
    if url_file is not None:
        paths.append(Path(url_file).resolve())
    if len(set(paths)) != len(paths):
        raise ValueError("Database, output, log and input files must be different.")
    resources = {
        Path(database).resolve(): "database",
        Path(output).resolve(): "output",
        progress_path.resolve(): "progress output",
        Path(log_file).resolve(): "log",
    }
    lock_paths = {Path(str(path) + ".lock") for path in resources}
    if any(path in lock_paths for path in paths):
        raise ValueError("Data files must not overlap resource lock files.")
    with ExitStack() as stack:
        for path in sorted(resources):
            stack.enter_context(crawl_lock(path, resources[path]))
        logger = stack.enter_context(crawl_logging(Path(log_file)))
        record = stack.enter_context(RunRecord(database, output, operation))
        try:
            with (
                CrawlStore(database) as store,
                HttpCache(cache_path) as cache,
                RawArchive(raw_path) as raw,
                HttpClient(policy, cache=cache) as client,
            ):
                record.data["raw_directory"] = str(raw_path.resolve())
                if operation == "reparse" and not sum(store.counts().values()):
                    store.enqueue(raw.urls())
                    if not sum(store.counts().values()):
                        raise ValueError("No archived courses to reparse.")
                if operation == "refresh" and not sum(store.counts().values()):
                    raise ValueError(
                        "No known courses to refresh; discover courses first."
                    )
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
                elif operation == "discover" or (
                    operation == "resume" and not sum(store.counts().values())
                ):
                    logger.info("discovery_started")
                    store.enqueue(
                        discover_course_urls(
                            client.get_text,
                            delay_seconds=0,
                            baseline_count=load_baseline_count(output),
                            max_count_drop=max_count_drop,
                        )
                    )
                    logger.info(
                        "discovery_completed queued=%s", sum(store.counts().values())
                    )
                if operation in {"refresh", "reparse"}:
                    store.schedule_refresh()

                def fetch_course(url):
                    if operation == "reparse":
                        return raw.latest(url)
                    html = client.get_text(url)
                    raw.capture(url, html, record.run_id)
                    return html

                try:
                    summary = (
                        CrawlSummary(0, 0, 0)
                        if operation == "discover"
                        else crawl_courses(
                            store,
                            fetch_course,
                            limit=limit,
                            retry_failed=retry_failed,
                            commit_batch_size=commit_batch_size,
                            workers=workers,
                        )
                    )
                except BaseException:
                    # Best-effort export must not replace the task's failure or interrupt.
                    try:
                        export_run(store, output, record)
                    except Exception:
                        logger.exception("export_failed_during_abort")
                    raise
                else:
                    snapshot = export_run(store, output, record)
                record.data["run"] = asdict(summary)
                result = {
                    "run_id": record.run_id,
                    "metadata": str(record.path),
                    "published": record.data.get("published", False),
                    "export_path": record.data.get("export_path"),
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
