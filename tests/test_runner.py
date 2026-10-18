"""Application lifecycle, discovery-once behavior and persistent failure logs."""

import json
import logging
import unittest
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from crawler.http import HttpPolicy
from crawler.runner import crawl_lock, run_crawl
from crawler.storage import CrawlStore
from crawler.utm_course import course_url


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kwargs = dict(
            database=self.root / "state.sqlite3",
            output=self.root / "courses.json",
            log_file=self.root / "crawl.log",
            policy=HttpPolicy(interval=0),
        )
        self.urls = [course_url("CSC148H5"), course_url("CSC108H5")]
        self.fixtures = Path(__file__).parent / "fixtures"

    @patch("crawler.runner.HttpClient")
    @patch("crawler.runner.discover_course_urls")
    def test_discovers_once_and_resumes(self, discover, client_cls):
        discover.return_value = deque(self.urls)
        client = client_cls.return_value.__enter__.return_value
        client.get_text.side_effect = [
            (self.fixtures / "csc148h5.html").read_text(),
            (self.fixtures / "csc108h5.html").read_text(),
        ]
        first = run_crawl(**self.kwargs, limit=1)
        first_snapshot = Path(first["snapshot"])
        self.assertEqual(len(json.loads(first_snapshot.read_text())), 1)
        self.assertEqual(first["queue"]["pending"], 1)
        second = run_crawl(**self.kwargs)
        self.assertEqual(second["queue"]["succeeded"], 2)
        third = run_crawl(**self.kwargs)
        self.assertEqual(third["run"]["processed"], 0)
        self.assertEqual(second["snapshot"], third["snapshot"])
        self.assertEqual(len(json.loads(first_snapshot.read_text())), 1)
        self.assertEqual(client.get_text.call_count, 2)
        discover.assert_called_once_with(
            client.get_text, delay_seconds=0, baseline_count=None, max_count_drop=0.1
        )
        self.assertEqual(len(json.loads(self.kwargs["output"].read_text())), 2)
        self.assertIn("crawl_completed", self.kwargs["log_file"].read_text())

    @patch("crawler.runner.HttpClient")
    @patch("crawler.runner.discover_course_urls")
    def test_import_queue_and_failed_page_log(self, discover, client_cls):
        url_file = self.root / "urls.json"
        url_file.write_text(json.dumps(self.urls + [self.urls[0]]))
        client = client_cls.return_value.__enter__.return_value
        client.get_text.side_effect = [
            "Bad HTML",
            (self.fixtures / "csc108h5.html").read_text(),
        ]
        result = run_crawl(**self.kwargs, url_file=url_file)
        self.assertEqual(result["run"]["failed"], 1)
        self.assertEqual(result["queue"]["succeeded"], 1)
        discover.assert_not_called()
        text = self.kwargs["log_file"].read_text()
        self.assertIn(self.urls[0], text)
        self.assertIn("stage=parse", text)
        self.assertIn("Traceback", text)
        self.assertEqual(len(json.loads(self.kwargs["output"].read_text())), 1)

    @patch("crawler.runner.HttpClient")
    def test_interrupt_exports_committed_results(self, client_cls):
        with CrawlStore(self.kwargs["database"]) as store:
            store.enqueue(self.urls)
        client = client_cls.return_value.__enter__.return_value
        client.get_text.side_effect = [
            (self.fixtures / "csc148h5.html").read_text(),
            KeyboardInterrupt(),
        ]
        with self.assertRaises(KeyboardInterrupt):
            run_crawl(**self.kwargs)
        self.assertEqual(len(json.loads(self.kwargs["output"].read_text())), 1)
        with CrawlStore(self.kwargs["database"]) as store:
            self.assertEqual(store.counts()["pending"], 1)

    @patch("crawler.runner.HttpClient")
    def test_bad_import_does_not_overwrite_export(self, client_cls):
        path = self.root / "urls.json"
        self.kwargs["output"].write_text('["old"]')
        for payload in ["{}", "[]", "[1]", '["https://evil.test"]', "not json"]:
            path.write_text(payload)
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                run_crawl(**self.kwargs, url_file=path)
            self.assertEqual(self.kwargs["output"].read_text(), '["old"]')

    def test_conflicting_paths_and_limits(self):
        for field in ["output", "log_file"]:
            kwargs = dict(self.kwargs, **{field: self.kwargs["database"]})
            with self.subTest(field=field), self.assertRaises(ValueError):
                run_crawl(**kwargs)
        with self.assertRaises(ValueError):
            run_crawl(**self.kwargs, url_file=self.kwargs["output"])
        with self.assertRaises(ValueError):
            run_crawl(**self.kwargs, limit=0)

    def test_single_worker_lock(self):
        with crawl_lock(self.kwargs["database"]):
            with self.assertRaisesRegex(RuntimeError, "Another crawler"):
                with crawl_lock(self.kwargs["database"]):
                    self.fail("Concurrent worker should not acquire lock")
        with crawl_lock(self.kwargs["database"]):
            pass

    @patch("crawler.runner.HttpClient")
    @patch(
        "crawler.runner.discover_course_urls", side_effect=ValueError("bad discovery")
    )
    def test_logging_handlers_are_closed_on_failure(self, discover, client):
        logger = logging.getLogger("crawler")
        handlers, level = list(logger.handlers), logger.level
        with self.assertRaises(ValueError):
            run_crawl(**self.kwargs)
        self.assertEqual(logger.handlers, handlers)
        self.assertEqual(logger.level, level)

    @patch("crawler.runner.HttpClient")
    def test_export_failure_preserves_original_abort(self, client):
        with CrawlStore(self.kwargs["database"]) as store:
            store.enqueue(self.urls[:1])
        for primary in [
            KeyboardInterrupt("stop"),
            SystemExit(7),
            OSError("checkpoint failed"),
        ]:
            with self.subTest(primary=type(primary).__name__):
                with (
                    patch("crawler.runner.crawl_courses", side_effect=primary),
                    patch.object(
                        CrawlStore, "export", side_effect=OSError("export disk full")
                    ),
                ):
                    with self.assertRaises(type(primary)) as caught:
                        run_crawl(**self.kwargs)
                self.assertIs(caught.exception, primary)
        text = self.kwargs["log_file"].read_text()
        self.assertIn("export_failed_during_abort", text)
        self.assertIn("export disk full", text)
        self.assertIn("crawl_interrupted", text)

    @patch("crawler.runner.HttpClient")
    def test_export_failure_after_normal_completion_still_fails(self, client):
        with CrawlStore(self.kwargs["database"]) as store:
            store.enqueue(self.urls[:1])
            store.save_success(self.urls[0], {"url": self.urls[0]})
        failure = OSError("export disk full")
        with patch.object(CrawlStore, "export", side_effect=failure):
            with self.assertRaises(OSError) as caught:
                run_crawl(**self.kwargs)
        self.assertIs(caught.exception, failure)

    @patch("crawler.runner.HttpClient")
    @patch("crawler.runner.discover_course_urls")
    def test_explicit_refresh_only_reparses_known_courses(self, discover, client_cls):
        discover.return_value = self.urls[:1]
        html = (self.fixtures / "csc148h5.html").read_text()
        client = client_cls.return_value.__enter__.return_value
        client.get_text.return_value = html
        run_crawl(**self.kwargs)
        result = run_crawl(**self.kwargs, refresh=True)
        self.assertEqual(result["run"]["processed"], 1)
        self.assertEqual(discover.call_count, 1)
        self.assertEqual(client.get_text.call_count, 2)

    @patch("crawler.runner.HttpClient")
    @patch("crawler.runner.discover_course_urls")
    def test_discovery_operation_only_adds_new_tasks(self, discover, client_cls):
        with CrawlStore(self.kwargs["database"]) as store:
            store.enqueue(self.urls[:1])
            store.save_success(self.urls[0], {"url": self.urls[0]})
        discover.return_value = self.urls
        result = run_crawl(**self.kwargs, operation="discover")
        self.assertEqual(result["run"]["processed"], 0)
        self.assertEqual(result["queue"], {"pending": 1, "succeeded": 1, "failed": 0})
        client_cls.return_value.__enter__.return_value.get_text.assert_not_called()

    @patch("crawler.runner.HttpClient")
    def test_refresh_empty_queue_is_rejected(self, client):
        with self.assertRaisesRegex(ValueError, "No known courses"):
            run_crawl(**self.kwargs, operation="refresh")
        client.return_value.__enter__.return_value.get_text.assert_not_called()
