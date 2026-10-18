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
            crawl_courses(
                store, fetch, parse_course=lambda url: {"url": url}, retry_failed=True
            )
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
                crawl_courses(
                    store,
                    fetch,
                    parse_course=lambda url: {"url": url},
                    retry_failed=True,
                )
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

    def test_pending_tasks_cannot_be_starved_by_failed_task(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            store.save_failure(self.urls[0], "404")
            for expected in self.urls[1:]:
                self.assertEqual(store.work_urls(1, retry_failed=True), [expected])
                crawl_courses(
                    store,
                    lambda url: url,
                    parse_course=lambda url: {"url": url},
                    limit=1,
                    retry_failed=True,
                )
            self.assertEqual(store.work_urls(), [])
            self.assertEqual(store.work_urls(retry_failed=True), [self.urls[0]])

    def test_failed_tasks_require_explicit_retry_and_run_once(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            store.save_failure(self.urls[0], "404")
            fetch = Mock(side_effect=requests.HTTPError("404"))
            self.assertEqual(crawl_courses(store, fetch).processed, 0)
            fetch.assert_not_called()
            with self.assertLogs("crawler.pipeline", level="ERROR"):
                summary = crawl_courses(store, fetch, retry_failed=True)
            self.assertEqual((summary.processed, summary.failed), (1, 1))
            fetch.assert_called_once_with(self.urls[0])

    def test_unchanged_export_is_reused_across_restarts(self):
        output = self.path / "courses.json"
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            store.save_success(self.urls[0], {"url": self.urls[0]})
            first = store.export(output)
        with (
            CrawlStore(self.database) as store,
            patch(
                "crawler.storage.write_json_array_atomic",
                side_effect=AssertionError("unnecessary serialization"),
            ),
        ):
            self.assertEqual(store.export(output), first)
            store.enqueue(self.urls[1:])
            store.save_failure(self.urls[1], "404")
            self.assertEqual(store.export(output), first)
            first.unlink()
            self.assertEqual(store.export(output), first)
            self.assertTrue(first.exists())

    def test_export_cache_detects_manual_edits_and_missing_output(self):
        output = self.path / "courses.json"
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            store.save_success(self.urls[0], {"url": self.urls[0]})
            first = store.export(output)
            output.write_text('["edited"]')
            self.assertEqual(store.export(output), first)
            self.assertEqual(json.loads(output.read_text()), [{"url": self.urls[0]}])
            self.assertEqual(len(list(first.parent.glob("*.json"))), 2)
            output.unlink()
            self.assertEqual(store.export(output), first)

    def test_export_cache_invalidates_on_results_and_detects_corruption(self):
        output = self.path / "courses.json"
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            store.save_success(self.urls[0], {"url": self.urls[0]})
            first = store.export(output)
            store.save_success(self.urls[0], {"url": self.urls[0], "name": "changed"})
            second = store.export(output)
            self.assertNotEqual(first, second)
            second.write_text("corrupt")
            with self.assertRaisesRegex(OSError, "integrity"):
                store.export(output)

    def test_direct_database_changes_invalidate_cached_export(self):
        output = self.path / "courses.json"
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            store.save_success(self.urls[0], {"url": self.urls[0]})
            store.export(output)
            with store.connection:
                store.connection.execute("DELETE FROM courses")
            store.export(output)
            self.assertEqual(json.loads(output.read_text()), [])

    def test_streamed_queue_membership_is_frozen_during_updates(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:2])
            with store.work_queue(retry_failed=True, batch_size=1) as queue:
                self.assertEqual(queue.total, 2)
                self.assertEqual(next(queue.urls), self.urls[0])
                store.save_failure(self.urls[0], "retry later")
                store.enqueue(self.urls[2:])
                self.assertEqual(list(queue.urls), [self.urls[1]])
            self.assertEqual(store.work_urls(), self.urls[1:])

    def test_streamed_queue_validates_batch_size_and_closes_on_abort(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            for size in [0, -1, True, 1.5]:
                with self.subTest(size=size), self.assertRaises(ValueError):
                    with store.work_queue(batch_size=size):
                        pass
            with self.assertRaises(KeyboardInterrupt):
                with store.work_queue(batch_size=1) as queue:
                    next(queue.urls)
                    raise KeyboardInterrupt()
            self.assertEqual(store.work_urls(), self.urls)

    def test_task_selection_uses_status_index_without_sorting(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            store.save_failure(self.urls[0], "404")
            statements = []
            store.connection.set_trace_callback(statements.append)
            self.assertEqual(
                store.work_urls(3, retry_failed=True), self.urls[1:] + self.urls[:1]
            )
            store.connection.set_trace_callback(None)
            queries = [
                sql
                for sql in statements
                if sql.startswith("INSERT INTO run_queue(url) SELECT")
            ]
            self.assertTrue(queries)
            for query in queries:
                plan = " ".join(
                    row[3]
                    for row in store.connection.execute("EXPLAIN QUERY PLAN " + query)
                )
                self.assertIn("courses_status", plan)
                self.assertNotIn("SCAN courses", plan)
                self.assertNotIn("TEMP B-TREE", plan)

    def test_optional_batch_commit_boundaries_and_tail(self):
        import sqlite3

        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            observer = sqlite3.connect(self.database)
            self.addCleanup(observer.close)

            def count():
                return observer.execute(
                    "SELECT COUNT(*) FROM courses WHERE status='succeeded'"
                ).fetchone()[0]

            with store.batch_writes(2):
                store.save_success(self.urls[0], {"url": self.urls[0]})
                self.assertEqual(count(), 0)
                store.save_success(self.urls[1], {"url": self.urls[1]})
                self.assertEqual(count(), 2)
                store.save_success(self.urls[2], {"url": self.urls[2]})
                self.assertEqual(count(), 2)
            self.assertEqual(count(), 3)

    def test_batch_error_rolls_back_only_uncommitted_results(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            with self.assertRaises(RuntimeError):
                with store.batch_writes(2):
                    for url in self.urls:
                        store.save_success(url, {"url": url})
                    raise RuntimeError("fatal")
            self.assertEqual(
                store.counts(), {"pending": 1, "failed": 0, "succeeded": 2}
            )
            with self.assertRaises(KeyboardInterrupt):
                with store.batch_writes(5):
                    store.save_success(self.urls[2], {"url": self.urls[2]})
                    raise KeyboardInterrupt()
            self.assertEqual(store.counts()["succeeded"], 3)

    def test_batch_configuration_and_hard_crash_recovery(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            for size in [0, -1, True, 1.5]:
                with self.subTest(size=size), self.assertRaises(ValueError):
                    with store.batch_writes(size):
                        pass
        script = """
import os,sys
from pathlib import Path
from crawler.storage import CrawlStore
with CrawlStore(Path(sys.argv[1])) as store:
 with store.batch_writes(5):
  store.save_success(sys.argv[2],{'url':sys.argv[2]})
  os._exit(9)
"""
        process = subprocess.run(
            [sys.executable, "-c", script, str(self.database), self.urls[0]],
            capture_output=True,
        )
        self.assertEqual(process.returncode, 9)
        with CrawlStore(self.database) as store:
            self.assertEqual(store.counts()["pending"], 1)

    def test_concurrent_fetch_is_bounded_and_database_has_one_writer(self):
        import threading
        from threading import Event, Lock

        gate = Event()
        lock = Lock()
        active = 0
        peak = 0

        def fetch(url):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    gate.set()
            if not gate.wait(2):
                raise RuntimeError("No overlap")
            with lock:
                active -= 1
            return url

        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)
            save = store.save_success

            def save_on_owner(url, result):
                self.assertIs(threading.current_thread(), threading.main_thread())
                save(url, result)

            with patch.object(store, "save_success", side_effect=save_on_owner):
                result = crawl_courses(
                    store, fetch, parse_course=lambda url: {"url": url}, workers=2
                )
            self.assertEqual(result.succeeded, 3)
            self.assertEqual(peak, 2)

    def test_concurrent_page_failure_is_isolated(self):
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls)

            def fetch(url):
                if url == self.urls[0]:
                    raise requests.Timeout("offline")
                return url

            with self.assertLogs("crawler.pipeline", level="ERROR"):
                result = crawl_courses(
                    store,
                    fetch,
                    parse_course=lambda url: {"url": url},
                    workers=2,
                    commit_batch_size=2,
                )
            self.assertEqual((result.succeeded, result.failed), (2, 1))
            self.assertEqual(
                store.counts(), {"pending": 0, "succeeded": 2, "failed": 1}
            )

    def test_invalid_worker_count_stops_before_fetch(self):
        with CrawlStore(self.database) as store:
            for workers in [0, 17, True, 1.5]:
                with self.subTest(workers=workers), self.assertRaises(ValueError):
                    crawl_courses(store, Mock(), workers=workers)

    def test_refresh_retains_last_good_result_when_fetch_fails(self):
        output = self.path / "courses.json"
        with CrawlStore(self.database) as store:
            store.enqueue(self.urls[:1])
            store.save_success(self.urls[0], {"url": self.urls[0], "name": "previous"})
            first = store.export(output)
            store.schedule_refresh()
            with self.assertLogs("crawler.pipeline", level="ERROR"):
                crawl_courses(store, Mock(side_effect=requests.Timeout()))
            self.assertEqual(store.counts()["failed"], 1)
            self.assertEqual(store.export(output), first)
            self.assertEqual(json.loads(output.read_text())[0]["name"], "previous")

    def test_existing_database_constraint_is_migrated_without_data_loss(self):
        import sqlite3

        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """CREATE TABLE courses (url TEXT PRIMARY KEY,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,result TEXT,error TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,CHECK ((status = 'succeeded') = (result IS NOT NULL)))"""
            )
            connection.execute(
                "INSERT INTO courses(url,status,result) VALUES (?,?,?)",
                (self.urls[0], "succeeded", json.dumps({"url": self.urls[0]})),
            )
        with CrawlStore(self.database) as store:
            store.schedule_refresh()
            store.save_failure(self.urls[0], "timeout")
            self.assertEqual(store.counts()["failed"], 1)
            store.export(self.path / "courses.json")
            self.assertEqual(
                json.loads((self.path / "courses.json").read_text()),
                [{"url": self.urls[0]}],
            )
