"""Use-case substitution without SQLite, UTM parsing, or network clients."""

import logging
import unittest
from contextlib import contextmanager
from unittest.mock import Mock

from course_factory import course_record

from application.crawl import crawl_courses
from application.models import WorkQueue
from domain.course import validate_course_model


class MemoryStore:
    """Minimal port adapter; deliberately has no CrawlStore base class."""

    def __init__(self, urls):
        self.urls = urls
        self.saved = {}
        self.failures = {}
        self.closed = False

    @contextmanager
    def work_queue(self, limit=None, *, retry_failed=False):
        selected = self.urls if limit is None else self.urls[:limit]
        try:
            yield WorkQueue(len(selected), iter(selected))
        finally:
            self.closed = True

    @contextmanager
    def batch_writes(self, size=1):
        yield

    def save_success(self, url, course):
        self.saved[url] = course

    def save_failure(self, url, error):
        self.failures[url] = error


class ApplicationTests(unittest.TestCase):
    def test_use_case_accepts_different_campus_parser_and_storage(self):
        url = "https://example.test/course/csc148h1"
        store = MemoryStore([url])
        parser = Mock(return_value=course_record(url, campus="UTSG"))
        summary = crawl_courses(store, lambda _: "html", parse_course=parser)
        self.assertEqual(
            (summary.processed, summary.succeeded, summary.failed), (1, 1, 0)
        )
        self.assertEqual(store.saved[url].campus, "UTSG")
        self.assertTrue(store.closed)
        parser.assert_called_once_with("html")

    def test_source_validator_is_used_and_failure_does_not_block_next_page(self):
        urls = ["https://example.test/first", "https://example.test/second"]
        store = MemoryStore(urls)

        def source_validator(value, expected_url=None):
            if expected_url == urls[0]:
                raise ValueError("source identity rejected")
            return validate_course_model(value, expected_url)

        logger = logging.getLogger("test.application")
        with self.assertLogs(logger, level="ERROR"):
            summary = crawl_courses(
                store,
                lambda url: url,
                parse_course=course_record,
                validator=source_validator,
                workers=2,
                logger=logger,
            )
        self.assertEqual((summary.succeeded, summary.failed), (1, 1))
        self.assertIn("source identity rejected", store.failures[urls[0]])
        self.assertIn(urls[1], store.saved)

    def test_default_validator_rejects_mismatched_queue_identity(self):
        store = MemoryStore(["https://example.test/expected"])
        with self.assertLogs("application.crawl", level="ERROR"):
            result = crawl_courses(
                store,
                lambda _: "",
                parse_course=lambda _: course_record("https://example.test/other"),
            )
        self.assertEqual(result.failed, 1)
        self.assertFalse(store.saved)

    def test_storage_failure_propagates_and_closes_queue(self):
        store = MemoryStore(["https://example.test/course"])
        store.save_success = Mock(side_effect=OSError("storage failure"))
        with self.assertRaisesRegex(OSError, "storage failure"):
            crawl_courses(store, lambda url: url, parse_course=course_record)
        self.assertTrue(store.closed)

    def test_empty_queue_does_not_call_parser(self):
        parser = Mock()
        result = crawl_courses(MemoryStore([]), Mock(), parse_course=parser)
        self.assertEqual(result.processed, 0)
        parser.assert_not_called()
