"""Pydantic invariants and evidence-based DOM classification boundaries."""

import json
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from course_factory import course_record
from pydantic import BaseModel, ValidationError

from crawler.fields import FieldIndex
from crawler.utm_course import parse_course_document, parse_course_page
from crawler.validation import validate_utm_course_model
from domain.course import Course, CourseFieldError, validate_course_model
from domain.field_state import FieldState, FieldStatus

FIXTURES = Path(__file__).parent / "fixtures"
BASE_HTML = (
    '<h1 class="page-title">CSC148H5 • Name</h1>'
    '<div class="field field--name-field-desc"><div class="field__item">Description</div></div>'
)


def prerequisite(body):
    return (
        '<div class="field field--name-field-prerequisite">'
        '<label class="field__label">Prerequisites</label>' + body + "</div>"
    )


class PydanticCourseTests(unittest.TestCase):
    def setUp(self):
        self.legacy = course_record("https://utm.calendar.utoronto.ca/course/csc148h5")

    def test_model_is_pydantic_and_does_not_invent_null_provenance(self):
        course = Course.from_legacy(self.legacy)
        self.assertIsInstance(course, BaseModel)
        self.assertIs(course.field_states.prerequisites.status, FieldStatus.UNKNOWN)
        self.assertIs(course.field_states.description.status, FieldStatus.PRESENT)
        self.assertEqual(course.to_legacy_dict(), self.legacy)
        self.assertNotIn("field_states", self.legacy)

    def test_legacy_explicit_blank_is_empty_and_normalized(self):
        for blank in ["", "  ", "\n\t", "\u00a0"]:
            with self.subTest(blank=blank):
                course = Course.from_legacy({**self.legacy, "prerequisites": blank})
                self.assertIsNone(course.prerequisites)
                self.assertIs(
                    course.field_states.prerequisites.status, FieldStatus.EMPTY
                )

    def test_strict_types_unknown_keys_and_missing_nullable_key_are_rejected(self):
        cases = [
            {**self.legacy, "name": value}
            for value in [1, True, b"Name", [], None, " "]
        ]
        cases.extend(
            [{**self.legacy, "extra": "unknown"}, {**self.legacy, "prerequisites": 108}]
        )
        missing = dict(self.legacy)
        del missing["prerequisites"]
        cases.append(missing)
        for invalid in cases:
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                Course.from_legacy(invalid)

    def test_json_roundtrip_retains_observed_status_and_identity(self):
        course = parse_course_document(
            BASE_HTML + prerequisite('<div class="field__item">CSC108H5</div>')
        )
        restored = Course.model_validate_json(course.model_dump_json())
        self.assertEqual(restored, course)
        self.assertEqual(Course.model_validate(course.model_dump(mode="json")), course)
        self.assertEqual(restored.schema_version, 1)
        self.assertIs(restored.field_states.prerequisites.status, FieldStatus.PRESENT)
        self.assertIs(restored.field_states.exclusions.status, FieldStatus.NOT_PROVIDED)
        with self.assertRaises(ValueError):
            validate_course_model(course, expected_url="https://example.test/other")

    def test_state_and_value_must_agree(self):
        original = Course.from_legacy(self.legacy).model_dump(mode="json")
        for status, value, error in [
            ("present", None, None),
            ("present", "", None),
            ("empty", "CSC108H5", None),
            ("not_provided", "CSC108H5", None),
            ("unknown", "CSC108H5", None),
            ("parse_failed", "CSC108H5", "broken"),
            ("parse_failed", None, None),
            ("parse_failed", None, "  "),
            ("empty", None, "unexpected"),
            ("unsupported", None, None),
        ]:
            payload = json.loads(json.dumps(original))
            payload["prerequisites"] = value
            payload["field_states"]["prerequisites"] = {
                "status": status,
                "error": error,
            }
            with (
                self.subTest(status=status, value=value, error=error),
                self.assertRaises(ValidationError),
            ):
                Course.model_validate(payload)

    def test_status_and_version_do_not_coerce_bytes_booleans_or_numbers(self):
        for value in [b"empty", True, 1, None]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                FieldState(status=value)
        for value in [True, 1.0, "1"]:
            payload = Course.from_legacy(self.legacy).model_dump(mode="json")
            payload["schema_version"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Course.model_validate(payload)

    def test_schema_requires_complete_evidence_and_rejects_future_version(self):
        payload = Course.from_legacy(self.legacy).model_dump(mode="json")
        del payload["field_states"]["prerequisites"]
        with self.assertRaises(ValidationError):
            Course.model_validate(payload)
        payload = Course.from_legacy(self.legacy).model_dump(mode="json")
        payload["schema_version"] = 99
        with self.assertRaises(ValidationError):
            Course.model_validate(payload)
        schema = Course.model_json_schema()
        self.assertIn("field_states", schema["required"])
        self.assertIn("prerequisites", schema["required"])

    def test_nested_evidence_is_immutable_and_unvalidated_copies_are_rechecked(self):
        course = Course.from_legacy(self.legacy)
        with self.assertRaises(ValidationError):
            course.code = "CHANGED"
        with self.assertRaises(ValidationError):
            course.field_states.prerequisites.status = FieldStatus.EMPTY
        bad_state = course.field_states.model_copy(
            update={"prerequisites": FieldState(status=FieldStatus.PRESENT)}
        )
        tampered = course.model_copy(update={"field_states": bad_state})
        with self.assertRaises(ValidationError):
            validate_course_model(tampered)

    def test_parse_failure_is_inspectable_but_not_accepted_as_success(self):
        course = parse_course_document(BASE_HTML + prerequisite(""))
        self.assertIsNone(course.prerequisites)
        self.assertIs(
            course.field_states.prerequisites.status, FieldStatus.PARSE_FAILED
        )
        self.assertIs(
            Course.model_validate_json(
                course.model_dump_json()
            ).field_states.prerequisites.status,
            FieldStatus.PARSE_FAILED,
        )
        for validate in [validate_course_model, validate_utm_course_model]:
            with (
                self.subTest(validate=validate.__name__),
                self.assertRaises(CourseFieldError),
            ):
                validate(course)
        with self.assertRaises(CourseFieldError):
            parse_course_page(BASE_HTML + prerequisite(""))


class FieldEvidenceTests(unittest.TestCase):
    def test_missing_empty_present_and_malformed_dom_are_distinct(self):
        cases = [
            ("", FieldStatus.NOT_PROVIDED, None),
            (
                prerequisite('<div class="field__item"> &nbsp; </div>'),
                FieldStatus.EMPTY,
                None,
            ),
            (
                prerequisite('<div class="field__item"><a>CSC108H5</a></div>'),
                FieldStatus.PRESENT,
                "CSC108H5",
            ),
            (prerequisite(""), FieldStatus.PARSE_FAILED, None),
        ]
        for markup, status, value in cases:
            with self.subTest(markup=markup):
                course = parse_course_document(BASE_HTML + markup)
                self.assertIs(course.field_states.prerequisites.status, status)
                self.assertEqual(course.prerequisites, value)

    def test_duplicate_or_unowned_labels_do_not_borrow_values(self):
        for markup in [
            prerequisite('<div class="field__item">A</div>') * 2,
            '<label class="field__label">Prerequisites</label><div class="field__item">A</div>',
        ]:
            with self.subTest(markup=markup):
                result = FieldIndex(BeautifulSoup(markup, "html.parser")).observe(
                    "Prerequisites"
                )
                self.assertIs(result.state.status, FieldStatus.PARSE_FAILED)
                self.assertIsNone(result.value)

    def test_known_source_container_without_label_is_not_missing(self):
        for body in [
            '<div class="field__item">CSC108H5</div>',
            '<label class="field__label">Renamed field</label><div class="field__item">A</div>',
        ]:
            markup = (
                '<div class="field field--name-field-prerequisite">' + body + "</div>"
            )
            result = parse_course_document(BASE_HTML + markup)
            self.assertIs(
                result.field_states.prerequisites.status, FieldStatus.PARSE_FAILED
            )

    def test_nested_other_fields_are_neither_values_nor_owned_items(self):
        nested = (
            '<div class="field"><label class="field__label">Exclusions</label>'
            '<div class="field__item">WRONG</div></div>'
        )
        missing_item = parse_course_document(BASE_HTML + prerequisite(nested))
        self.assertIs(
            missing_item.field_states.prerequisites.status, FieldStatus.PARSE_FAILED
        )
        valid_item = parse_course_document(
            BASE_HTML
            + prerequisite('<div class="field__item">CSC108H5' + nested + "</div>")
        )
        self.assertEqual(valid_item.prerequisites, "CSC108H5")
        self.assertIs(valid_item.field_states.prerequisites.status, FieldStatus.PRESENT)

    def test_text_outside_value_items_is_not_silently_discarded(self):
        for content in [
            'CSC108H5<div class="field__item"> </div>',
            '<div class="field__item">CSC108H5</div> or MAT102H5',
        ]:
            course = parse_course_document(BASE_HTML + prerequisite(content))
            self.assertIs(
                course.field_states.prerequisites.status, FieldStatus.PARSE_FAILED
            )
            self.assertIsNone(course.prerequisites)

    def test_multivalue_and_whitespace_keep_original_semantics(self):
        markup = prerequisite(
            '<div class="field__items"><div class="field__item"> A&nbsp; B </div><div class="field__item">C</div></div>'
        )
        course = parse_course_document(BASE_HTML + markup)
        self.assertEqual(course.prerequisites, "A B; C")
        self.assertIs(course.field_states.prerequisites.status, FieldStatus.PRESENT)

    def test_blank_query_is_rejected_for_observation(self):
        with self.assertRaises(ValueError):
            FieldIndex(BeautifulSoup("", "html.parser")).observe(" : ")

    def test_required_description_failure_keeps_its_observed_state(self):
        title = '<h1 class="page-title">CSC148H5 • Name</h1>'
        for markup, expected in [
            ("", FieldStatus.NOT_PROVIDED),
            (
                '<div class="field field--name-field-desc"><div class="field__item"> </div></div>',
                FieldStatus.EMPTY,
            ),
            (
                '<div class="field field--name-field-desc"></div>',
                FieldStatus.PARSE_FAILED,
            ),
            (BASE_HTML[len(title) :] * 2, FieldStatus.PARSE_FAILED),
        ]:
            with (
                self.subTest(expected=expected),
                self.assertRaises(CourseFieldError) as caught,
            ):
                parse_course_document(title + markup)
            self.assertEqual(
                caught.exception.field_states["description"]["status"], expected.value
            )

    def test_full_official_documents_have_observed_not_inferred_status(self):
        for path in (FIXTURES / "full").glob("*.html"):
            with self.subTest(course=path.stem):
                course = parse_course_document(path.read_text())
                self.assertEqual(
                    course.to_legacy_dict(),
                    parse_course_page((FIXTURES / path.name).read_text()),
                )
                for field in type(course.field_states).model_fields:
                    state = getattr(course.field_states, field)
                    self.assertNotIn(
                        state.status, (FieldStatus.UNKNOWN, FieldStatus.PARSE_FAILED)
                    )
        ant = parse_course_document((FIXTURES / "full/ant102h5.html").read_text())
        self.assertIs(ant.field_states.prerequisites.status, FieldStatus.NOT_PROVIDED)
