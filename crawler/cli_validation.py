"""Validate explicitly supplied options before constructing application services."""

import argparse

CRAWL_ONLY = {
    "database",
    "log_file",
    "url_file",
    "limit",
    "retry_failed",
    "refresh",
    "operation",
    "raw_directory",
    "http_cache",
    "workers",
    "commit_batch_size",
}
NETWORK = {"interval", "timeout", "retries", "user_agent", "http_cache"}
PROCESSING = {"limit", "retry_failed", "workers", "commit_batch_size"}


def parse_checked(parser):
    # Suppressed defaults distinguish omission from an explicitly supplied default.
    defaults = {action.dest: action.default for action in parser._actions}
    for action in parser._actions:
        action.default = argparse.SUPPRESS
    args = parser.parse_args()
    supplied = set(vars(args))
    for dest, default in defaults.items():
        if dest not in supplied and default != argparse.SUPPRESS:
            setattr(args, dest, default)

    invalid = set()
    if not args.crawl:
        invalid |= supplied & CRAWL_ONLY
        if not args.discover:
            invalid |= supplied & {"max_count_drop"}
    else:
        if args.refresh and "operation" in supplied:
            parser.error("--refresh cannot be combined with --operation.")
        operation = "refresh" if args.refresh else args.operation
        if operation == "reparse":
            invalid |= supplied & (NETWORK | {"url_file", "max_count_drop"})
        elif operation == "discover":
            invalid |= supplied & PROCESSING
            if args.url_file is not None:
                invalid |= supplied & (NETWORK | {"max_count_drop"})
        elif operation == "refresh":
            invalid |= supplied & {"url_file", "max_count_drop"}
    if (args.crawl or args.discover) and "course_code" in supplied:
        parser.error("course_code is only valid in single-course mode.")
    if invalid:
        names = ", ".join("--" + name.replace("_", "-") for name in sorted(invalid))
        parser.error(f"Options do not apply to the selected mode: {names}")
    return args
