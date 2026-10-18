"""Catalog discovery contracts and failure boundaries."""

import json
import unittest
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from crawler.discovery import (
    SEARCH_URL,
    DiscoveryError,
    discover_course_urls,
    extract_course_urls,
    load_baseline_count,
    normalize_course_url,
    parse_search_page,
)
from crawler.utm_course import course_url


def page(codes=("CSC148H5",), next_href=None, current=0):
    headings = "".join(
        f'<h3 class="js-views-accordion-group-header">{code} • Name</h3>'
        for code in codes
    )
    pager = (
        f'<li class="pager__item--next"><a href="{next_href}">Next</a></li>'
        if next_href
        else ""
    )
    return f'<div class="view-content">{headings}</div><nav class="pager"><a aria-current="page" href="?page={current}">Current</a>{pager}</nav>'


class DiscoveryTests(unittest.TestCase):
    def test_url_normalization(self):
        for href in [
            "/course/CSC148H5",
            "/course/csc148h5/?x=1#top",
            "https://utm.calendar.utoronto.ca/course/csc148h5",
            "course/csc148h5",
        ]:
            with self.subTest(href=href):
                self.assertEqual(normalize_course_url(href), course_url("CSC148H5"))
        for href in [
            "",
            "#top",
            "/course/csc148h1",
            "/course/csc148h5/extra",
            "http://utm.calendar.utoronto.ca/course/csc148h5",
            "//evil.com/course/csc148h5",
            "https://utm.calendar.utoronto.ca.evil.com/course/csc148h5",
            "javascript:alert(1)",
            "/course/%63sc148h5",
            "https://[invalid",
        ]:
            with self.subTest(href=href):
                self.assertIsNone(normalize_course_url(href))

    def test_link_deduplication(self):
        html = '<a href="/course/CSC148H5">A</a><a href="/course/csc148h5#x">B</a><a href="/course/mat102h5">C</a><a>No href</a><a href="/about">Other</a>'
        self.assertEqual(
            extract_course_urls(html), [course_url("CSC148H5"), course_url("MAT102H5")]
        )
        self.assertEqual(extract_course_urls(""), [])

    def test_official_first_page(self):
        html = (Path(__file__).parent / "fixtures/search-first.html").read_text()
        result = parse_search_page(html)
        self.assertEqual(len(result.course_urls), 30)
        self.assertEqual(result.course_urls[0], course_url("ANT101H5"))
        self.assertEqual(result.next_url, SEARCH_URL + "?page=1")

    def test_official_last_page(self):
        html = (Path(__file__).parent / "fixtures/search-last.html").read_text()
        result = parse_search_page(html, SEARCH_URL + "?page=83")
        self.assertEqual(result.course_urls, (course_url("WRI499Y5"),))
        self.assertIsNone(result.next_url)

    def test_headings_are_authoritative(self):
        html = page() + '<a href="/course/mat102h5">Prerequisite</a>'
        self.assertEqual(parse_search_page(html).course_urls, (course_url("CSC148H5"),))

    def test_traversal_order_and_global_dedup(self):
        pages = {
            SEARCH_URL + "?page=0": page(("CSC148H5", "MAT102H5"), "?page=1"),
            SEARCH_URL + "?page=1": page(("MAT102H5", "CSC108H5"), current=1),
        }
        fetch = Mock(side_effect=pages.__getitem__)
        queue = discover_course_urls(fetch, delay_seconds=0)
        self.assertIsInstance(queue, deque)
        self.assertEqual(
            list(queue),
            [course_url(code) for code in ["CSC148H5", "MAT102H5", "CSC108H5"]],
        )
        self.assertEqual(fetch.call_count, 2)

    def test_invalid_structure_and_pagination(self):
        for html in [
            "",
            page(()),
            page(("NotACourse",)),
            page(next_href="https://evil.com/course-search?page=1"),
            page(next_href="?page=-1"),
            page(next_href="?page=1&page=2"),
            page(next_href="?page=1&filter=x"),
            page() + '<li class="pager__item--last"><a href="?page=2">Last</a></li>',
            page(next_href="?page=1") * 2,
        ]:
            with self.subTest(html=html), self.assertRaises(DiscoveryError):
                parse_search_page(html)

    def test_cycles_and_limits_fail(self):
        with self.assertRaisesRegex(DiscoveryError, "cycle"):
            discover_course_urls(lambda url: page(next_href="?page=0"), delay_seconds=0)
        with self.assertRaisesRegex(DiscoveryError, "max_pages"):
            discover_course_urls(
                lambda url: page(next_href="?page=1"), delay_seconds=0, max_pages=1
            )
        for kwargs in [
            {"delay_seconds": -1},
            {"max_pages": 0},
            {"max_pages": 1.5},
            {"delay_seconds": float("nan")},
            {"delay_seconds": float("inf")},
            {"max_pages": True},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                discover_course_urls(**kwargs)

    def test_failure_on_later_page(self):
        fetch = Mock(side_effect=[page(next_href="?page=1"), requests.Timeout()])
        with self.assertRaises(requests.Timeout):
            discover_course_urls(fetch, delay_seconds=0)

    @patch("crawler.discovery.HttpClient")
    def test_http_contract(self, client_cls):
        client = client_cls.return_value.__enter__.return_value
        client.get_text.side_effect = [page(next_href="?page=1"), page(current=1)]
        self.assertEqual(len(discover_course_urls()), 1)
        client.get_text.assert_any_call(SEARCH_URL + "?page=0")
        client_cls.assert_called_once()
        self.assertEqual(client_cls.call_args.args[0].interval, 1.0)
        client.get_text.side_effect = requests.HTTPError()
        with self.assertRaises(requests.HTTPError):
            discover_course_urls()

    @patch("crawler.discovery.time.sleep")
    def test_injected_fetcher_throttle(self, sleep):
        fetch = Mock(side_effect=[page(next_href="?page=1"), page(current=1)])
        discover_course_urls(fetch)
        sleep.assert_called_once_with(1.0)

    def test_missing_or_inconsistent_pagination_is_rejected(self):
        html = (Path(__file__).parent / "fixtures/search-first.html").read_text()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        soup.select_one("nav.pager").decompose()
        with self.assertRaisesRegex(DiscoveryError, "pagination"):
            parse_search_page(str(soup))
        for html in [
            page(current=1),
            page().replace('aria-current="page"', ""),
            page(next_href="?page=2"),
        ]:
            with self.subTest(html=html), self.assertRaises(DiscoveryError):
                parse_search_page(html)

    def test_early_termination_is_rejected_across_pages(self):
        first = page(next_href="?page=1").replace(
            "</nav>", '<a href="?page=2">3</a></nav>'
        )
        fetch = Mock(side_effect=[first, page(current=1)])
        with self.assertRaisesRegex(DiscoveryError, "final page changed"):
            discover_course_urls(fetch, delay_seconds=0)

    def test_historical_decline_threshold_and_growth(self):
        for count, baseline, allowed, fails in [
            (90, 100, 0.1, False),
            (89, 100, 0.1, True),
            (101, 100, 0.1, False),
            (2, 3, 1 / 3, False),
            (1, 100, 1, False),
            (1, None, 0.1, False),
        ]:
            codes = tuple(f"CSC{100 + i:03}H5" for i in range(count))
            with self.subTest(count=count, baseline=baseline):
                if fails:
                    with self.assertRaisesRegex(DiscoveryError, "count fell"):
                        discover_course_urls(
                            lambda url: page(codes),
                            delay_seconds=0,
                            baseline_count=baseline,
                            max_count_drop=allowed,
                        )
                else:
                    self.assertEqual(
                        len(
                            discover_course_urls(
                                lambda url: page(codes),
                                delay_seconds=0,
                                baseline_count=baseline,
                                max_count_drop=allowed,
                            )
                        ),
                        count,
                    )

    def test_invalid_baseline_configuration(self):
        for kwargs in [
            {"baseline_count": 0},
            {"baseline_count": True},
            {"max_count_drop": -0.1},
            {"max_count_drop": float("nan")},
            {"max_count_drop": 1.1},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                discover_course_urls(**kwargs)

    def test_baseline_queue_and_result_formats(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            self.assertIsNone(load_baseline_count(path))
            for payload in [
                [course_url("CSC148H5")] * 2,
                [{"url": course_url("CSC148H5")}],
            ]:
                path.write_text(json.dumps(payload))
                self.assertEqual(load_baseline_count(path), 1)
            for payload in [{}, ["bad"], [{"code": "CSC148H5"}], [None]]:
                path.write_text(json.dumps(payload))
                with self.assertRaises(DiscoveryError):
                    load_baseline_count(path)
            path.write_text("[]")
            self.assertIsNone(load_baseline_count(path))

    def test_host_case_and_default_port_are_canonicalized(self):
        variants = [
            "https://UTM.CALENDAR.UTORONTO.CA/course/CSC148H5",
            "https://utm.calendar.utoronto.ca:443/course/csc148h5",
            "https://UTM.CALENDAR.UTORONTO.CA:443/course/CSC148H5/?x=1#top",
        ]
        html = "".join(f'<a href="{url}">Course</a>' for url in variants)
        self.assertEqual(extract_course_urls(html), [course_url("CSC148H5")])

    def test_invalid_origins_and_nonascii_codes_are_rejected(self):
        for url in [
            "https://user@utm.calendar.utoronto.ca/course/csc148h5",
            "https://user:password@utm.calendar.utoronto.ca/course/csc148h5",
            "https://utm.calendar.utoronto.ca:80/course/csc148h5",
            "https://utm.calendar.utoronto.ca:/course/csc148h5",
            "https://utm.calendar.utoronto.ca:bad/course/csc148h5",
            "https://utm.calendar.utoronto.ca:99999/course/csc148h5",
            "https://utm.calendar.utoronto.ca.evil.test/course/csc148h5",
            "/course/CSC١٤٨H5",
            "/course/CSC１４８H5",
            "/course/CſC148H5",
            None,
        ]:
            with self.subTest(url=url):
                self.assertIsNone(normalize_course_url(url))

    def test_pagination_uses_same_origin_normalization(self):
        valid = page(
            next_href="https://UTM.CALENDAR.UTORONTO.CA:443/course-search?page=1"
        )
        self.assertEqual(
            parse_search_page(
                valid, "https://UTM.CALENDAR.UTORONTO.CA:443/course-search"
            ).next_url,
            SEARCH_URL + "?page=1",
        )
        for origin in [
            "https://user@utm.calendar.utoronto.ca",
            "https://utm.calendar.utoronto.ca:80",
            "https://utm.calendar.utoronto.ca:bad",
        ]:
            with self.subTest(origin=origin), self.assertRaises(DiscoveryError):
                parse_search_page(page(next_href=origin + "/course-search?page=1"))
