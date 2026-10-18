"""Discover UTM course URLs without fetching individual course pages."""

from collections import deque
from dataclasses import dataclass
import math
import re
import time
from typing import Callable, Optional
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from crawler.utm_course import BASE_URL, course_url

SEARCH_URL = f"{BASE_URL}/course-search"


class DiscoveryError(ValueError):
    """The catalog cannot be traversed completely or safely."""


def normalize_course_url(href: str, page_url: str = SEARCH_URL) -> Optional[str]:
    """Accept only official UTM course paths; remove query/fragment variants."""
    try:
        url = urlsplit(urljoin(page_url, href.strip()))
        if url.scheme != 'https' or url.netloc != urlsplit(BASE_URL).netloc:
            return None
        match = re.fullmatch(r'/course/([^/]+)/?', url.path)
        return course_url(match[1]) if match else None
    except ValueError:
        return None


def extract_course_urls(html: str, page_url: str = SEARCH_URL) -> list[str]:
    """Extract and deduplicate valid course links in first-seen order."""
    soup = BeautifulSoup(html, 'html.parser')
    urls = (normalize_course_url(a['href'], page_url) for a in soup.select('a[href]'))
    return list(dict.fromkeys(url for url in urls if url is not None))


@dataclass(frozen=True)
class SearchPage:
    """Parsed catalog entries and the optional next-page URL."""

    course_urls: tuple[str, ...]
    next_url: Optional[str]


def _page_url(href: str, current_url: str) -> str:
    """Canonicalize unfiltered search pagination; reject unexpected targets."""
    url = urlsplit(urljoin(current_url, href))
    query = parse_qs(url.query, keep_blank_values=True)
    if (url.scheme != 'https' or url.netloc != urlsplit(BASE_URL).netloc
            or url.path != '/course-search' or set(query) != {'page'}
            or len(query['page']) != 1 or not query['page'][0].isascii()
            or not query['page'][0].isdigit()):
        raise DiscoveryError('Unexpected search pagination URL.')
    return f"{SEARCH_URL}?page={int(query['page'][0])}"


def parse_search_page(html: str, page_url: str = SEARCH_URL) -> SearchPage:
    """Read result headings, not prerequisite links embedded in result bodies.

    Current UTM headings have no anchors, so derive URLs from their codes.
    A missing/empty result structure fails instead of returning a partial queue.
    """
    soup = BeautifulSoup(html, 'html.parser')
    headings = soup.select('.view-content h3.js-views-accordion-group-header')
    if not headings:
        raise DiscoveryError('Course search result headings not found.')
    urls = []
    for heading in headings:
        code = heading.get_text(' ', strip=True).split('•', 1)[0].strip()
        try:
            urls.append(course_url(code))
        except ValueError as error:
            raise DiscoveryError(f'Invalid course result heading: {code!r}') from error
    next_links = soup.select('.pager__item--next a[href]')
    if len(next_links) > 1:
        raise DiscoveryError('Multiple next-page links found.')
    next_url = _page_url(next_links[0]['href'], page_url) if next_links else None
    if next_url is None and soup.select('.pager__item--last a[href]'):
        raise DiscoveryError('Pagination contains a last page but no next page.')
    return SearchPage(tuple(dict.fromkeys(urls)), next_url)


def discover_course_urls(
    fetch_page: Optional[Callable[[str], str]] = None,
    *,
    delay_seconds: float = 1.0,
    max_pages: int = 1000,
) -> deque[str]:
    """Return an ordered unique FIFO queue after traversing the whole catalog.

    Inject fetch_page for offline tests. HTTP and parsing failures propagate;
    no partial queue is returned. max_pages is a safety limit, not a page count.
    """
    if (not math.isfinite(delay_seconds) or delay_seconds < 0
            or type(max_pages) is not int or max_pages < 1):
        raise ValueError('Delay must be nonnegative and max_pages must be positive.')
    with requests.Session() as session:
        def fetch(url: str) -> str:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response.text

        fetcher = fetch_page if fetch_page is not None else fetch
        current = f'{SEARCH_URL}?page=0'
        visited = set()
        courses: dict[str, None] = {}
        while current is not None:
            if current in visited:
                raise DiscoveryError('Pagination cycle detected.')
            if len(visited) >= max_pages:
                raise DiscoveryError('Search pagination exceeded max_pages.')
            if visited and delay_seconds:
                time.sleep(delay_seconds)
            page = parse_search_page(fetcher(current), current)
            visited.add(current)
            courses.update(dict.fromkeys(page.course_urls))
            current = page.next_url
    return deque(courses)
