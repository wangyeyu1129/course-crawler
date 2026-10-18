"""Small CLI entry point; importing this module never starts a request."""

import argparse
import json
from pathlib import Path

from crawler.discovery import discover_course_urls
from crawler.utm_course import fetch_course_page, parse_course_page


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a UTM course or discover the course catalog.")
    parser.add_argument("course_code", nargs="?", default="CSC148H5")
    parser.add_argument("--discover", action="store_true", help="Discover all UTM course URLs.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = (list(discover_course_urls()) if args.discover
              else parse_course_page(fetch_course_page(args.course_code)))
    payload = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
