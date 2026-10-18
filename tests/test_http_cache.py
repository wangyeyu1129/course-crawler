"""Conditional GET, request variants, persistence and cache failure behavior."""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from crawler.http import HttpClient, HttpPolicy
from crawler.http_cache import HttpCache

URL = "https://utm.calendar.utoronto.ca/course/csc148h5"


def response(status=200, body="course HTML", **headers):
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("utf-8")
    result._content_consumed = True
    result.encoding = "utf-8"
    result.headers.update(headers)
    result.url = URL
    return result


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cache.sqlite3"
        self.session = requests.Session()
        self.addCleanup(self.session.close)

    def test_etag_and_last_modified_reuse_304_body(self):
        with (
            HttpCache(self.path) as cache,
            HttpClient(
                HttpPolicy(interval=0), session=self.session, cache=cache
            ) as client,
        ):
            with patch.object(
                self.session,
                "get",
                side_effect=[
                    response(
                        ETag='W/"one"',
                        **{"Last-Modified": "Sat, 17 Oct 2026 03:38:32 GMT"},
                    ),
                    response(304, body=""),
                ],
            ) as get:
                self.assertEqual(client.get_text(URL), "course HTML")
                with patch.object(cache, "put", wraps=cache.put) as put:
                    self.assertEqual(client.get_text(URL), "course HTML")
                    put.assert_not_called()
                self.assertEqual(
                    get.call_args.kwargs["headers"]["If-None-Match"], 'W/"one"'
                )
                self.assertIn("If-Modified-Since", get.call_args.kwargs["headers"])
                self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_last_modified_only_cache_survives_reopen(self):
        with (
            HttpCache(self.path) as cache,
            patch.object(
                self.session,
                "get",
                return_value=response(
                    **{"Last-Modified": "Sat, 17 Oct 2026 03:38:32 GMT"}
                ),
            ),
        ):
            HttpClient(
                HttpPolicy(interval=0), session=self.session, cache=cache
            ).get_text(URL)
        with (
            HttpCache(self.path) as cache,
            patch.object(
                self.session, "get", return_value=response(304, body="")
            ) as get,
        ):
            body = HttpClient(
                HttpPolicy(interval=0), session=self.session, cache=cache
            ).get_text(URL)
            self.assertEqual(body, "course HTML")
            self.assertNotIn("If-None-Match", get.call_args.kwargs["headers"])
            self.assertIn("If-Modified-Since", get.call_args.kwargs["headers"])

    def test_cookie_and_user_agent_variants_are_separate(self):
        with (
            HttpCache(self.path) as cache,
            patch.object(
                self.session,
                "get",
                side_effect=[
                    response(ETag='"one"', Vary="Cookie"),
                    response(ETag='"two"'),
                    response(ETag='"three"'),
                ],
            ) as get,
        ):
            client = HttpClient(
                HttpPolicy(interval=0), session=self.session, cache=cache
            )
            client.get_text(URL)
            self.session.cookies.set("variant", "one")
            client.get_text(URL)
            self.assertNotIn("If-None-Match", get.call_args.kwargs["headers"])
            HttpClient(
                HttpPolicy(interval=0, user_agent="different-bot"),
                session=self.session,
                cache=cache,
            ).get_text(URL)
            self.assertNotIn("If-None-Match", get.call_args.kwargs["headers"])

    def test_uncacheable_responses_and_changed_content(self):
        for headers in [
            {},
            {"ETag": '"one"', "Cache-Control": "no-store"},
            {"ETag": '"one"', "Vary": " * "},
        ]:
            with (
                self.subTest(headers=headers),
                HttpCache(self.path) as cache,
                patch.object(
                    self.session,
                    "get",
                    side_effect=[response(**headers), response(body="new")],
                ) as get,
            ):
                client = HttpClient(
                    HttpPolicy(interval=0), session=self.session, cache=cache
                )
                client.get_text(URL)
                self.assertEqual(client.get_text(URL), "new")
                self.assertNotIn("If-None-Match", get.call_args.kwargs["headers"])

    def test_corrupt_cached_body_forces_full_request(self):
        with (
            HttpCache(self.path) as cache,
            patch.object(
                self.session,
                "get",
                side_effect=[
                    response(ETag='"one"'),
                    response(body="fresh", ETag='"two"'),
                ],
            ) as get,
        ):
            client = HttpClient(
                HttpPolicy(interval=0), session=self.session, cache=cache
            )
            client.get_text(URL)
            with cache.connection:
                cache.connection.execute("UPDATE http_pages SET body='corrupt'")
            self.assertEqual(client.get_text(URL), "fresh")
            self.assertNotIn("If-None-Match", get.call_args.kwargs["headers"])

    def test_cache_write_error_does_not_discard_fresh_page(self):
        with (
            HttpCache(self.path) as cache,
            patch.object(self.session, "get", return_value=response(ETag='"one"')),
        ):
            client = HttpClient(
                HttpPolicy(interval=0), session=self.session, cache=cache
            )
            with (
                patch.object(
                    cache, "put", side_effect=sqlite3.OperationalError("disk full")
                ),
                self.assertLogs("crawler.http", level="ERROR"),
            ):
                self.assertEqual(client.get_text(URL), "course HTML")
