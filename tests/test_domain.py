"""Shared shape invariants and legacy adapter validation stay distinct."""

import unittest
from collections import UserDict
from unittest.mock import patch

from course_factory import course_record

from application.models import CrawlSummary, WorkQueue
from crawler.models import Course as LegacyCourse
from crawler.models import validate_course as legacy_validate
from crawler.pipeline import CrawlSummary as LegacySummary
from crawler.run_records import code_identity
from crawler.storage import WorkQueue as LegacyQueue
from domain.course import Course, validate_course


class DomainTests(unittest.TestCase):
    def test_source_rules_are_not_embedded_in_shared_contract(self):
        value = course_record("https://example.test/course/csc148h1", campus="UTSG")
        result = validate_course(UserDict(value))
        self.assertEqual(value, result)
        self.assertIsNot(value, result)
        with self.assertRaises(ValueError):
            legacy_validate(value)

    def test_required_text_shape_and_types_remain_enforced(self):
        value = course_record("https://example.test/course")
        cases = [None, [], {}, {**value, "extra": "field"}]
        for key in ("code", "name", "campus", "description", "url"):
            cases.extend({**value, key: empty} for empty in (None, "", "  ", 1))
        cases.append({**value, "exclusions": []})
        for invalid in cases:
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                validate_course(invalid)

    def test_legacy_exports_are_identical_shared_types(self):
        self.assertIs(LegacyCourse, Course)
        self.assertIs(LegacySummary, CrawlSummary)
        self.assertIs(LegacyQueue, WorkQueue)

    def test_parser_fingerprint_includes_migrated_model_and_adapter_rules(self):
        from pathlib import Path

        original = Path.read_bytes
        before = code_identity()["parser_version"]
        for source in ("domain/course.py", "crawler/validation.py"):

            def changed(path, target=source):
                content = original(path)
                return (
                    content + b"\n# changed"
                    if path.as_posix().endswith(target)
                    else content
                )

            with self.subTest(source=source), patch.object(Path, "read_bytes", changed):
                self.assertNotEqual(code_identity()["parser_version"], before)
