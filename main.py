"""Small CLI entry point; importing this module never starts a request."""

import argparse
import json

from crawler.utm_course import fetch_course_page, parse_course_page


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse one UTM calendar course.")
    parser.add_argument("course_code", nargs="?", default="CSC148H5")
    args = parser.parse_args()
    print(json.dumps(parse_course_page(fetch_course_page(args.course_code)), indent=2))


if __name__ == "__main__":
    main()
