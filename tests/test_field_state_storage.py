"""Field evidence survives persistence; failed extraction never becomes success."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from course_factory import course_record

from crawler.pipeline import crawl_courses
from crawler.runner import run_crawl
from crawler.storage import CrawlStore
from crawler.utm_course import course_url, parse_course_document, parse_course_page
from domain.course import CourseFieldError, validate_course_model
from domain.field_state import FieldStatus

BASE = (
    '<h1 class="page-title">CSC148H5 • Name</h1>'
    '<div class="field field--name-field-desc"><div class="field__item">Description</div></div>'
)
BROKEN = (
    BASE + '<div class="field"><label class="field__label">Prerequisites</label></div>'
)
EMPTY = (
    BASE
    + '<div class="field"><label class="field__label">Prerequisites</label><div class="field__item"> </div></div>'
)


class FieldStateStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.output = self.root / "courses.json"
        self.url = course_url("CSC148H5")

    def test_success_stores_evidence_and_legacy_export_remains_flat(self):
        with CrawlStore(self.database) as store:
            store.enqueue([self.url])
            store.save_success(self.url, parse_course_document(EMPTY))
            stored = json.loads(
                store.connection.execute("SELECT result FROM courses").fetchone()[0]
            )
            self.assertEqual(stored["schema_version"], 1)
            self.assertEqual(stored["field_states"]["prerequisites"]["status"], "empty")
            self.assertEqual(
                stored["field_states"]["exclusions"]["status"], "not_provided"
            )
            store.export(self.output)
        self.assertEqual(
            json.loads(self.output.read_text()), [parse_course_page(EMPTY)]
        )
        with CrawlStore(self.database) as store:
            value = json.loads(
                store.connection.execute("SELECT result FROM courses").fetchone()[0]
            )
            self.assertIs(
                validate_course_model(value).field_states.prerequisites.status,
                FieldStatus.EMPTY,
            )

    def test_legacy_null_is_unknown_without_rewriting_historical_row(self):
        legacy = course_record(self.url)
        with CrawlStore(self.database) as store:
            store.enqueue([self.url])
            store.connection.execute(
                "UPDATE courses SET status='succeeded',result=? WHERE url=?",
                (json.dumps(legacy), self.url),
            )
            store.connection.commit()
            before = store.connection.execute("SELECT result FROM courses").fetchone()[
                0
            ]
            model = validate_course_model(json.loads(before))
            self.assertIs(model.field_states.prerequisites.status, FieldStatus.UNKNOWN)
            store.export(self.output)
            after = store.connection.execute("SELECT result FROM courses").fetchone()[0]
            self.assertEqual(before, after)
            self.assertEqual(json.loads(self.output.read_text()), [legacy])

    def test_direct_save_rejects_parse_failure_without_changing_task(self):
        with CrawlStore(self.database) as store:
            store.enqueue([self.url])
            with self.assertRaises(CourseFieldError):
                store.save_success(self.url, parse_course_document(BROKEN))
            row = store.connection.execute(
                "SELECT status,attempts,result FROM courses"
            ).fetchone()
            self.assertEqual(tuple(row), ("pending", 0, None))

    def test_pipeline_persists_structured_error_and_continues(self):
        next_url = course_url("CSC108H5")
        next_html = (Path(__file__).parent / "fixtures/csc108h5.html").read_text()
        with CrawlStore(self.database) as store:
            store.enqueue([self.url, next_url])
            with self.assertLogs("crawler.pipeline", level="ERROR"):
                summary = crawl_courses(
                    store, lambda url: BROKEN if url == self.url else next_html
                )
            self.assertEqual((summary.failed, summary.succeeded), (1, 1))
            row = store.connection.execute(
                "SELECT result,error FROM courses WHERE url=?", (self.url,)
            ).fetchone()
            self.assertIsNone(row["result"])
            error = json.loads(row["error"])
            self.assertEqual(error["stage"], "parse")
            self.assertEqual(
                error["field_states"]["prerequisites"]["status"], "parse_failed"
            )
            self.assertTrue(error["field_states"]["prerequisites"]["error"])

    def test_refresh_failure_preserves_previous_good_values_and_evidence(self):
        with CrawlStore(self.database) as store:
            store.enqueue([self.url])
            store.save_success(self.url, parse_course_document(EMPTY))
            original = store.connection.execute(
                "SELECT result FROM courses"
            ).fetchone()[0]
            store.schedule_refresh()
            with self.assertLogs("crawler.pipeline", level="ERROR"):
                crawl_courses(store, lambda _: BROKEN)
            row = store.connection.execute(
                "SELECT status,result,error FROM courses"
            ).fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["result"], original)
            self.assertEqual(
                json.loads(row["error"])["field_states"]["prerequisites"]["status"],
                "parse_failed",
            )

    def test_metadata_only_change_invalidates_revision_but_reuses_legacy_bytes(self):
        with CrawlStore(self.database) as store:
            store.enqueue([self.url])
            store.save_success(self.url, parse_course_document(BASE))
            snapshot = store.export(self.output)
            revision = store.connection.execute(
                "SELECT revision FROM dataset_version"
            ).fetchone()[0]
            store.save_success(self.url, parse_course_document(EMPTY))
            self.assertGreater(
                store.connection.execute(
                    "SELECT revision FROM dataset_version"
                ).fetchone()[0],
                revision,
            )
            self.assertEqual(store.export(self.output), snapshot)

    def test_old_export_cache_cannot_skip_new_projection_validation(self):
        from crawler.snapshots import file_digest
        from crawler.storage import write_json_atomic

        legacy = course_record(self.url, prerequisites="  ")
        with CrawlStore(self.database) as store:
            store.enqueue([self.url])
            store.connection.execute(
                "UPDATE courses SET status='succeeded',result=?", (json.dumps(legacy),)
            )
            store.connection.commit()
            revision = store.connection.execute(
                "SELECT revision FROM dataset_version"
            ).fetchone()[0]
            write_json_atomic(self.output, [legacy])
            store.connection.execute(
                "INSERT INTO export_cache VALUES (?,?,?,1)",
                (str(self.output.resolve()), revision, file_digest(self.output)),
            )
            store.connection.commit()
            store.export(self.output)
        self.assertIsNone(json.loads(self.output.read_text())[0]["prerequisites"])

    def test_invalid_persisted_evidence_cannot_replace_previous_export(self):
        with CrawlStore(self.database) as store:
            store.enqueue([self.url])
            store.save_success(self.url, parse_course_document(BASE))
            store.export(self.output)
            before = self.output.read_bytes()
            tampered = parse_course_document(BASE).model_dump(mode="json")
            tampered["field_states"]["prerequisites"]["status"] = "present"
            store.connection.execute(
                "UPDATE courses SET result=?", (json.dumps(tampered),)
            )
            store.connection.commit()
            with self.assertRaises(ValueError):
                store.export(self.output)
            self.assertEqual(self.output.read_bytes(), before)

    def test_partial_run_with_bad_field_does_not_replace_published_output(self):
        from unittest.mock import patch

        from crawler.raw_data import RawArchive

        with CrawlStore(self.database) as store:
            store.enqueue([self.url])
            store.save_success(self.url, parse_course_document(EMPTY))
            store.export(self.output)
        previous = self.output.read_bytes()
        raw_path = self.root / "raw"
        with RawArchive(raw_path) as raw:
            raw.capture(self.url, BROKEN, "seed")
        with (
            patch("crawler.runner.HttpClient"),
            self.assertLogs("crawler", level="ERROR"),
        ):
            result = run_crawl(
                database=self.database,
                output=self.output,
                log_file=self.root / "crawl.log",
                raw_directory=raw_path,
                operation="reparse",
            )
        self.assertFalse(result["published"])
        self.assertEqual(result["queue"]["failed"], 1)
        self.assertEqual(self.output.read_bytes(), previous)
