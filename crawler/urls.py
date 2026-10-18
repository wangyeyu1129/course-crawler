"""Shared UTM URL and ASCII course-code rules."""

import re
from typing import Optional
from urllib.parse import SplitResult, urljoin, urlsplit

BASE_URL = "https://utm.calendar.utoronto.ca"
SEARCH_URL = f"{BASE_URL}/course-search"
_ORIGIN = urlsplit(BASE_URL)


def course_url(course_code: str) -> str:
    """Build a canonical URL from an ASCII UTM course code."""
    if not isinstance(course_code, str):
        raise ValueError("Invalid UTM course code.")
    raw_code = course_code.strip()
    if not raw_code.isascii():
        raise ValueError("UTM course codes must contain only ASCII characters.")
    code = raw_code.upper()
    if not re.fullmatch(r"[A-Z]{3}[0-9]{3}[HY]5", code):
        raise ValueError("Invalid UTM course code.")
    return f"{BASE_URL}/course/{code.lower()}"


def resolve_utm_url(href: str, page_url: str = SEARCH_URL) -> SplitResult:
    """Resolve official HTTPS links, accepting case-insensitive host and port 443.

    Credentials, other ports/hosts, control characters and malformed authority
    are rejected. The returned authority always uses the configured origin.
    """
    if not isinstance(href, str) or any(ord(c) < 32 or ord(c) == 127 for c in href):
        raise ValueError("Invalid UTM URL.")
    url = urlsplit(urljoin(page_url, href.strip()))
    if (
        url.scheme != "https"
        or url.hostname != _ORIGIN.hostname
        or url.port not in (None, 443)
        or url.username is not None
        or url.password is not None
        or url.netloc.endswith(":")
    ):
        raise ValueError("URL must use the official HTTPS origin without credentials.")
    return url._replace(netloc=_ORIGIN.netloc)


def normalize_course_url(href: str, page_url: str = SEARCH_URL) -> Optional[str]:
    """Canonicalize only official course paths; discard query and fragment."""
    try:
        url = resolve_utm_url(href, page_url)
        match = re.fullmatch(r"/course/([^/]+)/?", url.path)
        return course_url(match[1]) if match else None
    except ValueError:
        return None
