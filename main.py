"""Small CLI entry point; importing this module never starts a request."""

import argparse
import json
from pathlib import Path

from crawler.discovery import discover_course_urls
from crawler.http import HttpClient, HttpPolicy
from crawler.runner import run_crawl
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
    policy = HttpPolicy(
        user_agent=args.user_agent,
        read_timeout=args.timeout,
        interval=args.interval,
        retries=args.retries,
    )
    if args.crawl:
        result = run_crawl(
            database=args.database,
            output=args.output or Path("data/utm_courses.json"),
            log_file=args.log_file,
            url_file=args.url_file,
            limit=args.limit,
            policy=policy,
        )
        print(json.dumps(result, indent=2))
        return 1 if result["queue"]["failed"] else 0
    if args.discover:
        with HttpClient(policy) as client:
            result = list(discover_course_urls(client.get_text, delay_seconds=0))
    else:
        result = parse_course_page(fetch_course_page(args.course_code, policy=policy))
    payload = json.dumps(result, indent=2)
    if args.output:
        write_json_atomic(args.output, result)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
