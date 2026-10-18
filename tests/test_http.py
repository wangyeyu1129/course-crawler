"""Deterministic retry, timeout and rate-limit tests without network or sleep."""

import unittest
from email.utils import formatdate
from unittest.mock import Mock, patch

import requests

from crawler.http import HttpClient, HttpPolicy


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


def response(status=200, retry_after=None):
    value = requests.Response()
    value.status_code = status
    value.url = "https://example.test"
    value._content = b"html"
    value._content_consumed = True
    if retry_after is not None:
        value.headers["Retry-After"] = retry_after
    return value


class HttpTests(unittest.TestCase):
    def setUp(self):
        logger = patch("crawler.http.LOGGER")
        self.logger = logger.start()
        self.addCleanup(logger.stop)

    def client(self, outcomes, **kwargs):
        clock = Clock()
        session = Mock()
        session.get.side_effect = outcomes
        client = HttpClient(
            HttpPolicy(**kwargs),
            session=session,
            clock=clock.clock,
            sleep=clock.sleep,
            wall_clock=lambda: 1000,
        )
        return client, session, clock

    def test_headers_timeout_and_spacing(self):
        client, session, clock = self.client([response(), response()], interval=2)
        with client:
            self.assertEqual(client.get_text("url"), "html")
            client.get_text("url")
        self.assertEqual(clock.sleeps, [2])
        session.get.assert_called_with(
            "url",
            headers={"User-Agent": HttpPolicy().user_agent},
            timeout=(10.0, 30.0),
            allow_redirects=False,
        )
        session.close.assert_not_called()

    def test_transient_network_errors_retry(self):
        client, session, clock = self.client(
            [requests.Timeout(), requests.ConnectionError(), response()], interval=0
        )
        self.assertEqual(client.get_text("url"), "html")
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(clock.sleeps, [1, 2])
        self.assertEqual(self.logger.warning.call_count, 2)

    def test_broken_response_stream_retries(self):
        client, session, clock = self.client(
            [requests.exceptions.ChunkedEncodingError("truncated"), response()],
            interval=0,
        )
        self.assertEqual(client.get_text("url"), "html")
        self.assertEqual(session.get.call_count, 2)

    def test_all_retryable_statuses(self):
        for status in [429, 500, 502, 503, 504]:
            with self.subTest(status=status):
                client, session, clock = self.client(
                    [response(status), response()], interval=0
                )
                self.assertEqual(client.get_text("url"), "html")
                self.assertEqual(session.get.call_count, 2)

    def test_retry_exhaustion(self):
        for failure in [requests.Timeout(), response(503)]:
            outcomes = [failure] * 3
            client, session, clock = self.client(outcomes, retries=2, interval=0)
            with self.assertRaises(requests.RequestException):
                client.get_text("url")
            self.assertEqual(session.get.call_count, 3)

    def test_permanent_errors_do_not_retry(self):
        for status in [400, 401, 403, 404]:
            with self.subTest(status=status):
                client, session, clock = self.client([response(status)])
                with self.assertRaises(requests.HTTPError):
                    client.get_text("url")
                self.assertEqual(session.get.call_count, 1)

    def test_retry_after_seconds_date_and_invalid(self):
        for header, expected in [
            ("5", 5),
            (formatdate(1007, usegmt=True), 7),
            ("bad", 1),
            ("-1", 1),
            ("nan", 1),
        ]:
            with self.subTest(header=header):
                client, session, clock = self.client(
                    [response(429, header), response()], interval=0
                )
                client.get_text("url")
                self.assertEqual(clock.sleeps, [expected])

    def test_interval_and_backoff_are_not_added(self):
        client, session, clock = self.client(
            [response(503), response()], interval=5, backoff=2
        )
        client.get_text("url")
        self.assertEqual(clock.sleeps, [5])

    def test_policy_boundaries(self):
        for kwargs in [
            {"interval": -1},
            {"read_timeout": 0},
            {"connect_timeout": 0},
            {"interval": float("nan")},
            {"backoff": float("inf")},
            {"retries": -1},
            {"retries": 11},
            {"retries": True},
            {"user_agent": ""},
            {"user_agent": "bad\r\nheader"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                HttpPolicy(**kwargs)

    def test_final_retry_after_applies_to_next_url(self):
        client, session, clock = self.client(
            [response(429, "8"), response()], retries=0, interval=1
        )
        with self.assertRaises(requests.HTTPError):
            client.get_text("first")
        client.get_text("second")
        self.assertEqual(clock.sleeps, [8])

    @patch("crawler.http.requests.Session")
    def test_owned_session_closes_on_success_and_error(self, session_cls):
        session = session_cls.return_value
        session.get.return_value = response()
        with HttpClient(HttpPolicy(retries=0)) as client:
            self.assertEqual(client.get_text("url"), "html")
        session.close.assert_called_once()
        session.reset_mock()
        session.get.side_effect = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            with HttpClient(HttpPolicy(retries=0)) as client:
                client.get_text("url")
        session.close.assert_called_once()

    def test_zero_retries(self):
        client, session, clock = self.client([requests.Timeout()], retries=0)
        with self.assertRaises(requests.Timeout):
            client.get_text("url")
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(clock.sleeps, [])

    def test_redirects_are_rejected_without_following_or_retry(self):
        for status in [300, 301, 302, 303, 304, 305, 307, 308]:
            for location in [
                "/course/csc148h5",
                "https://evil.test/course",
                "http://utm.calendar.utoronto.ca/course/csc148h5",
            ]:
                with self.subTest(status=status, location=location):
                    redirected = response(status)
                    redirected.headers["Location"] = location
                    client, session, clock = self.client([redirected, response()])
                    with patch.object(
                        redirected, "close", wraps=redirected.close
                    ) as close:
                        with self.assertRaises(requests.HTTPError) as caught:
                            client.get_text(
                                "https://utm.calendar.utoronto.ca/course/csc148h5"
                            )
                        close.assert_called_once()
                    self.assertIs(caught.exception.response, redirected)
                    self.assertEqual(session.get.call_count, 1)
                    self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
                    self.assertEqual(clock.sleeps, [])

    def test_rejected_redirect_still_spaces_next_request(self):
        client, session, clock = self.client([response(302), response()], interval=2)
        with self.assertRaises(requests.HTTPError):
            client.get_text("first")
        self.assertEqual(client.get_text("second"), "html")
        self.assertEqual(clock.sleeps, [2])
        self.assertEqual(session.get.call_count, 2)
