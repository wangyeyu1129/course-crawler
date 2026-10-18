"""Persistent recovery, atomic result writes, and per-page failure isolation."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from crawler.pipeline import crawl_courses
from crawler.storage import CrawlStore, write_json_atomic
from crawler.utm_course import course_url


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.database = self.path / "checkpoint.sqlite3"
        self.urls = [course_url(code) for code in ["CSC148H5", "CSC108H5", "MAT102H5"]]

    def test_duplicate_normalization_and_batch_validation(self):
        with CrawlStore(self.database) as store:
            store.enqueue(
                [self.urls[0], self.urls[0].replace("csc148h5", "CSC148H5") + "/?a=1"]
            )
            self.assertEqual(store.counts()["pending"], 1)
            with self.assertRaises(ValueError):
                store.enqueue([self.urls[1], "https://evil.com/course/csc148h5"])
            self.assertEqual(store.work_urls(), [self.urls[0]])

    def test_success_failure_and_resume(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            fetch = Mock(
                side_effect=[self.urls[0], requests.Timeout("offline"), self.urls[2]]
            )
            with self.assertLogs("crawler.pipeline", level="INFO") as logs:
                summary = crawl_courses(
                    store, fetch, parse_course=lambda url: {"url": url}
                )
            self.assertEqual(
                (summary.processed, summary.succeeded, summary.failed), (3, 2, 1)
            )
            self.assertTrue(
                any(
                    "stage=fetch" in line and self.urls[1] in line
                    for line in logs.output
                )
            )
            self.assertEqual(
                store.counts(), {"pending": 0, "succeeded": 2, "failed": 1}
            )
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            fetch = Mock(return_value=self.urls[1])
            crawl_courses(store, fetch, parse_course=lambda url: {"url": url})
            fetch.assert_called_once_with(self.urls[1])
            row = store.connection.execute(
                "SELECT * FROM courses WHERE url=?", (self.urls[1],)
            ).fetchone()
            self.assertEqual(row["attempts"], 2)
            self.assertIsNone(row["error"])
            output = self.path / "nested/results.json"
            store.export(output)
            self.assertEqual(len(json.loads(output.read_text())), 3)
            self.assertEqual(store.counts()["succeeded"], 3)

    def test_unexpected_parse_error_is_isolated(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:2])
            parse = Mock(side_effect=[RuntimeError("bad DOM"), {"url": self.urls[1]}])
            with self.assertLogs("crawler.pipeline", level="ERROR") as logs:
                summary = crawl_courses(store, lambda url: url, parse_course=parse)
            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.succeeded, 1)
            self.assertIn("stage=parse", "\n".join(logs.output))

    def test_wrong_course_identity_fails(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            with self.assertLogs("crawler.pipeline", level="ERROR"):
                crawl_courses(
                    store,
                    lambda url: "",
                    parse_course=lambda html: {"url": self.urls[1]},
                )
            self.assertEqual(store.counts()["failed"], 1)

    def test_interrupt_preserves_committed_results(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            fetch = Mock(side_effect=[self.urls[0], KeyboardInterrupt()])
            with self.assertRaises(KeyboardInterrupt):
                crawl_courses(store, fetch, parse_course=lambda url: {"url": url})
        with CrawlStore(self.database) as store:
            self.assertEqual(
                store.counts(), {"pending": 2, "succeeded": 1, "failed": 0}
            )
            self.assertEqual(store.work_urls(), self.urls[1:])

    def test_process_crash_rolls_back_uncommitted_task(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:2])
            store.save_success(self.urls[0], {"url": self.urls[0]})
        script = """
import json, os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("UPDATE courses SET status='succeeded', result=? WHERE url=?",
                   (json.dumps({'url': sys.argv[2]}), sys.argv[2]))
os._exit(9)
"""
        process = subprocess.run(
            [sys.executable, "-c", script, str(self.database), self.urls[1]],
            check=False,
        )
        self.assertEqual(process.returncode, 9)
        with CrawlStore(self.database) as store:
            self.assertEqual(
                store.counts(), {"pending": 1, "succeeded": 1, "failed": 0}
            )
            self.assertEqual(store.work_urls(), [self.urls[1]])

    def test_checkpoint_failure_is_fatal_and_stays_pending(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            with patch.object(store, "save_success", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    crawl_courses(
                        store, lambda url: url, parse_course=lambda url: {"url": url}
                    )
            self.assertEqual(store.counts()["pending"], 3)
            self.assertEqual(store.counts()["failed"], 0)

    def test_limit_and_empty_queue(self):
        with CrawlStore(self.database) as store:
            self.assertEqual(crawl_courses(store, Mock()).processed, 0)
            store.enqueue(self.urls)
            summary = crawl_courses(
                store, lambda url: url, parse_course=lambda url: {"url": url}, limit=1
            )
            self.assertEqual(summary.processed, 1)
            self.assertEqual(store.counts()["pending"], 2)
            for limit in [0, -1, True, 1.5]:
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    store.work_urls(limit)

    def test_atomic_export_failure_preserves_previous_file(self):
        path = self.path / "results.json"
        path.write_text('["previous"]')
        with patch("crawler.storage.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_json_atomic(path, [{"new": "data"}])
        self.assertEqual(path.read_text(), '["previous"]')
        self.assertEqual(list(self.path.glob(".results.json.*")), [])

    def test_serialization_failure_does_not_complete_task(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            with self.assertRaises(TypeError):
                store.save_success(self.urls[0], {"value": object()})
            self.assertEqual(store.counts()["pending"], 1)

    def test_unknown_task_cannot_be_saved(self):
        with CrawlStore(self.database) as store:
            with self.assertRaises(ValueError):
                store.save_success(self.urls[0], {})
            with self.assertRaises(ValueError):
                store.save_failure(self.urls[0], "error")

    def test_title_only_page_fails_without_blocking_next_course(self):
        fixture = (Path(__file__).parent / "fixtures/csc108h5.html").read_text()
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:2])
            fetch = Mock(
                side_effect=['<h1 class="page-title">CSC148H5 • Name</h1>', fixture]
            )
            with self.assertLogs("crawler.pipeline", level="ERROR"):
                summary = crawl_courses(store, fetch)
            self.assertEqual((summary.succeeded, summary.failed), (1, 1))
            self.assertEqual(
                store.counts(), {"pending": 0, "succeeded": 1, "failed": 1}
            )

    def test_real_parser_pipeline(self):
        html = (Path(__file__).parent / "fixtures/csc148h5.html").read_text()
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            crawl_courses(store, lambda url: html)
            store.export(self.path / "courses.json")
            course = json.loads((self.path / "courses.json").read_text())[0]
            self.assertEqual(course["code"], "CSC148H5")
            self.assertEqual(course["prerequisites"], "CSC108H5")
