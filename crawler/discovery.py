"""Discover UTM course URLs without fetching individual course pages."""

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from crawler.http import HttpClient, HttpPolicy
from crawler.urls import SEARCH_URL, course_url, normalize_course_url, resolve_utm_url


class DiscoveryError(ValueError):
    """The catalog cannot be traversed completely or safely."""


def extract_course_urls(html: str, page_url: str = SEARCH_URL) -> list[str]:
    """Extract and deduplicate valid course links in first-seen order."""
    soup = BeautifulSoup(html, "html.parser")
    urls = (normalize_course_url(a["href"], page_url) for a in soup.select("a[href]"))
    return list(dict.fromkeys(url for url in urls if url is not None))


@dataclass(frozen=True)
class SearchPage:
    """Parsed catalog entries and the optional next-page URL."""

    course_urls: tuple[str, ...]
    next_url: Optional[str]
    last_page: int


def _page_url(href: str, current_url: str) -> str:
    """Canonicalize unfiltered search pagination; reject unexpected targets."""
    try:
        url = resolve_utm_url(href, current_url)
    except ValueError as error:
        raise DiscoveryError("Unexpected search pagination URL.") from error
    query = parse_qs(url.query, keep_blank_values=True)
    if (
        url.path != "/course-search"
        or set(query) != {"page"}
        or len(query["page"]) != 1
        or not query["page"][0].isascii()
        or not query["page"][0].isdigit()
    ):
        raise DiscoveryError("Unexpected search pagination URL.")
    return f"{SEARCH_URL}?page={int(query['page'][0])}"


def parse_search_page(html: str, page_url: str = SEARCH_URL) -> SearchPage:
    """Read result headings, not prerequisite links embedded in result bodies.

    Current UTM headings have no anchors, so derive URLs from their codes.
    A missing/empty result structure fails instead of returning a partial queue.
    """
    soup = BeautifulSoup(html, "html.parser")
    headings = soup.select(".view-content h3.js-views-accordion-group-header")
    if not headings:
        raise DiscoveryError("Course search result headings not found.")
    urls = []
    for heading in headings:
        code = heading.get_text(" ", strip=True).split("•", 1)[0].strip()
        try:
            urls.append(course_url(code))
        except ValueError as error:
            raise DiscoveryError(f"Invalid course result heading: {code!r}") from error
    pagers = soup.select("nav.pager")
    if len(pagers) != 1:
        raise DiscoveryError("Search pagination container not found or ambiguous.")
    pager = pagers[0]
    for selector in (".pager__item--next a[href]", ".pager__item--last a[href]"):
        if len(soup.select(selector)) != len(pager.select(selector)):
            raise DiscoveryError(
                "Pagination links found outside the pagination container."
            )
    active = pager.select('a[aria-current="page"][href]')
    if len(active) != 1:
        raise DiscoveryError("Current pagination marker not found or ambiguous.")
    requested = _page_url(page_url if urlsplit(page_url).query else "?page=0", page_url)
    current_url = _page_url(active[0]["href"], page_url)
    if current_url != requested:
        raise DiscoveryError(
            "Pagination current page does not match the requested page."
        )
    current_number = int(parse_qs(urlsplit(current_url).query)["page"][0])
    numbers = [
        int(parse_qs(urlsplit(_page_url(a["href"], page_url)).query)["page"][0])
        for a in pager.select("a[href]")
    ]
    last_page = max(numbers)
    next_links = pager.select(".pager__item--next a[href]")
    if len(next_links) > 1:
        raise DiscoveryError("Multiple next-page links found.")
    next_url = _page_url(next_links[0]["href"], page_url) if next_links else None
    if next_url:
        next_number = int(parse_qs(urlsplit(next_url).query)["page"][0])
        if next_number != current_number + 1:
            raise DiscoveryError(
                "Next page must be consecutive; possible pagination cycle."
            )
    elif last_page != current_number or pager.select(".pager__item--last a[href]"):
        raise DiscoveryError("Pagination is missing the next-page link.")
    return SearchPage(tuple(dict.fromkeys(urls)), next_url, last_page)


def load_baseline_count(path: Optional[Path]) -> Optional[int]:
    """Read unique course URLs from an earlier queue or course-result export."""
    if path is None or not Path(path).exists():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array.")
        urls = set()
        for row in data:
            raw_url = row.get("url") if isinstance(row, dict) else row
            url = normalize_course_url(raw_url) if isinstance(raw_url, str) else None
            if url is None:
                raise ValueError("Invalid baseline course URL.")
            urls.add(url)
        return len(urls) or None
    except (ValueError, TypeError, AttributeError) as error:
        raise DiscoveryError(f"Invalid discovery baseline: {path}") from error


def validate_count_drop(value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError("max_count_drop must be finite and between 0 and 1.")


def discover_course_urls(
    fetch_page: Optional[Callable[[str], str]] = None,
    *,
    delay_seconds: float = 1.0,
    max_pages: int = 1000,
    baseline_count: Optional[int] = None,
    max_count_drop: float = 0.1,
) -> deque[str]:
    """Return an ordered unique FIFO queue after traversing the whole catalog.

    Inject fetch_page for offline tests. HTTP and parsing failures propagate;
    no partial queue is returned. max_pages is a safety limit, not a page count.
    """
    if (
        not math.isfinite(delay_seconds)
        or delay_seconds < 0
        or type(max_pages) is not int
        or max_pages < 1
    ):
        raise ValueError("Delay must be nonnegative and max_pages must be positive.")
    validate_count_drop(max_count_drop)
    if baseline_count is not None and (
        type(baseline_count) is not int or baseline_count < 1
    ):
        raise ValueError("Invalid discovery baseline.")
    with HttpClient(HttpPolicy(interval=delay_seconds)) as client:
        fetcher = fetch_page if fetch_page is not None else client.get_text
        current = f"{SEARCH_URL}?page=0"
        visited = set()
        courses: dict[str, None] = {}
        expected_last = None
        while current is not None:
            if current in visited:
                raise DiscoveryError("Pagination cycle detected.")
            if len(visited) >= max_pages:
                raise DiscoveryError("Search pagination exceeded max_pages.")
            if fetch_page is not None and visited and delay_seconds:
                time.sleep(delay_seconds)
            page = parse_search_page(fetcher(current), current)
            if expected_last is None:
                expected_last = page.last_page
            elif page.last_page != expected_last:
                raise DiscoveryError("Advertised final page changed during discovery.")
            visited.add(current)
            courses.update(dict.fromkeys(page.course_urls))
            current = page.next_url
    if baseline_count is not None and len(courses) < baseline_count * (
        1 - max_count_drop
    ):
        raise DiscoveryError(
            f"Discovered course count fell from {baseline_count} to {len(courses)} beyond the allowed decline."
        )
    return deque(courses)
