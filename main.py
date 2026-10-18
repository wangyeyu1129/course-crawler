"""Small CLI entry point; importing this module never starts a request."""

import argparse
import json
from pathlib import Path

from crawler.discovery import (
    discover_course_urls,
    load_baseline_count,
    validate_count_drop,
)
from crawler.http import HttpClient, HttpPolicy
from crawler.runner import crawl_lock, run_crawl
from crawler.storage import write_json_atomic
from crawler.utm_course import fetch_course_page, parse_course_page


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse a UTM course or discover the course catalog."
    )
    parser.add_argument("course_code", nargs="?", default="CSC148H5")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--discover", action="store_true", help="Discover all UTM course URLs."
    )
    modes.add_argument(
        "--crawl", action="store_true", help="Crawl or resume the UTM catalog."
    )
    parser.add_argument("--database", type=Path, default=Path("data/utm_crawl.sqlite3"))
    parser.add_argument("--log-file", type=Path, default=Path("data/crawl.log"))
    parser.add_argument(
        "--url-file", type=Path, help="Import a JSON URL queue instead of discovery."
    )
    parser.add_argument(
        "--limit", type=int, help="Process at most this many unfinished URLs."
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry failed courses after pending courses.",
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--operation",
        choices=["resume", "discover", "refresh", "reparse"],
        default="resume",
    )
    parser.add_argument("--raw-directory", type=Path)
    parser.add_argument("--http-cache", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--commit-batch-size", type=int, default=1)
    parser.add_argument("--max-count-drop", type=float, default=0.1)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Read timeout in seconds."
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="Retries after the first request."
    )
    parser.add_argument("--user-agent", default=HttpPolicy().user_agent)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        policy = HttpPolicy(
            user_agent=args.user_agent,
            read_timeout=args.timeout,
            interval=args.interval,
            retries=args.retries,
        )
        if not 1 <= args.workers <= 16:
            raise ValueError("workers must be between 1 and 16.")
        if args.commit_batch_size < 1:
            raise ValueError("commit_batch_size must be positive.")
        validate_count_drop(args.max_count_drop)
        if args.limit is not None and args.limit < 1:
            raise ValueError("limit must be positive.")
    except ValueError as error:
        parser.error(str(error))
    if args.crawl:
        result = run_crawl(
            database=args.database,
            output=args.output or Path("data/utm_courses.json"),
            log_file=args.log_file,
            url_file=args.url_file,
            limit=args.limit,
            max_count_drop=args.max_count_drop,
            retry_failed=args.retry_failed,
            commit_batch_size=args.commit_batch_size,
            workers=args.workers,
            refresh=args.refresh,
            operation=args.operation,
            http_cache=args.http_cache,
            raw_directory=args.raw_directory,
            policy=policy,
        )
        print(json.dumps(result, indent=2))
        return 1 if result["queue"]["failed"] else 0
    if args.discover:
        with HttpClient(policy) as client:
            result = list(
                discover_course_urls(
                    client.get_text,
                    delay_seconds=0,
                    baseline_count=load_baseline_count(args.output),
                    max_count_drop=args.max_count_drop,
                )
            )
    else:
        result = parse_course_page(fetch_course_page(args.course_code, policy=policy))
    if args.output:
        with crawl_lock(args.output.resolve(), "output"):
            write_json_atomic(args.output, result)
    else:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
