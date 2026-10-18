"""Offline behavior tests using official DOM excerpts and boundary cases."""

import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from bs4 import BeautifulSoup

from crawler.fields import FieldIndex, get_labeled_field
from crawler.utm_course import course_url, fetch_course_page, parse_course_page

FIXTURES = Path(__file__).parent / "fixtures"


class FieldTests(unittest.TestCase):
    def test_field_boundaries(self):
        cases = [
            (
                '<label class="field__label">Prerequisites</label><div class="field__item"><a>A</a> or <b>B</b></div>',
                "A or B",
            ),
            (
                '<div class="field__label"> prerequisites : </div><div class="field__items"><div class="field__item">A</div><div class="field__item">B</div></div>',
                "A; B",
            ),
            (
                '<label class="field__label">Prerequisites</label><div class="field__item"> \n&nbsp; </div>',
                None,
            ),
            ('<label class="field__label">Prerequisites</label>', None),
            (
                '<label class="field__label">Recommended Prerequisites</label><div class="field__item">Wrong</div>',
                None,
            ),
            (
                '<label class="field__label">Prerequisites</label><div class="field"><div class="field__item">Wrong</div></div>',
                None,
            ),
            (
                '<label class="field__label"><b>Prerequisites</b></label><div class="field__item">A&nbsp; \n B</div>',
                "A B",
            ),
            (
                '<label class="field__label">Prerequisites</label><div class="field__item">A<span class="field__item">B</span></div>',
                "A B",
            ),
        ]
        for markup, expected in cases:
            with self.subTest(markup=markup):
                soup = BeautifulSoup(
                    '<div class="field">'
                    + markup
                    + '</div><div class="field"><div class="field__item">Neighbor</div></div>',
                    "html.parser",
                )
                self.assertEqual(get_labeled_field(soup, "Prerequisites"), expected)

    def test_nested_field_text_is_isolated_and_dom_is_unchanged(self):
        html = '<div class="field"><label class="field__label">Prerequisites</label><div class="field__item"><a>CSC108H5</a><div class="field"><label class="field__label">Exclusions</label><div class="field__item"><b>MAT102H5</b></div></div><p>or <em>CSC111H5</em></p></div></div>'
        soup = BeautifulSoup(html, "html.parser")
        before = str(soup)
        self.assertEqual(
            get_labeled_field(soup, "Prerequisites"), "CSC108H5 or CSC111H5"
        )
        self.assertEqual(get_labeled_field(soup, "Exclusions"), "MAT102H5")
        self.assertEqual(str(soup), before)

    def test_nested_fields_do_not_pollute_multivalue_items(self):
        html = '<div class="field"><label class="field__label">Prerequisites</label><div class="field__items"><div class="field__item">First<div class="field"><div class="field__item">Wrong</div></div>after</div><div class="field__item">Second<br/>line<span class="field__item">continued</span></div></div></div>'
        self.assertEqual(
            get_labeled_field(BeautifulSoup(html, "html.parser"), "Prerequisites"),
            "First after; Second line continued",
        )

    def test_item_containing_only_another_field_is_empty(self):
        html = '<div class="field"><label class="field__label">Prerequisites</label><div class="field__item"><div class="field"><div class="field__item">Wrong</div></div></div></div>'
        self.assertIsNone(
            get_labeled_field(BeautifulSoup(html, "html.parser"), "Prerequisites")
        )

    def test_index_scans_labels_once_and_preserves_first_match(self):
        soup = BeautifulSoup(
            '<div class="field"><label class="field__label"> Prerequisites: </label><div class="field__item">First</div></div><div class="field"><label class="field__label">Prerequisites</label><div class="field__item">Second</div></div>',
            "html.parser",
        )
        with patch.object(soup, "select", wraps=soup.select) as select:
            fields = FieldIndex(soup)
            self.assertEqual(fields.get("prerequisites"), "First")
            self.assertEqual(fields.get(" PREREQUISITES: "), "First")
            self.assertIsNone(fields.get("missing"))
            select.assert_called_once_with(".field__label")

    def test_blank_query(self):
        for label in ["", "  ", ":"]:
            with self.subTest(label=label), self.assertRaises(ValueError):
                get_labeled_field(BeautifulSoup("", "html.parser"), label)

    def test_missing_label(self):
        self.assertIsNone(
            get_labeled_field(BeautifulSoup("", "html.parser"), "Prerequisites")
        )


class CourseTests(unittest.TestCase):
    def test_official_courses(self):
        expected = {
            "csc148h5": ("Introduction to Computer Science", "CSC108H5", "38L/24P"),
            "csc108h5": (
                "Introduction to Computer Programming",
                "Grade 12 Advanced Functions (MHF4U).",
                "38L/24P",
            ),
            "csc207h5": (
                "Software Design",
                "(60% or higher in CSC111H5 ) or (60% or higher in CSC148H5 )(Only CSC111H5 or CSC148H5 taken at the UTM campus will be accepted.)",
                "24L/12P",
            ),
            "mat102h5": (
                "Introduction to Mathematical Proofs",
                "Minimum 70% in Grade 12 Advanced Functions (MHF4U)",
                "41L/12T",
            ),
            "ant102h5": (
                "Introduction to Sociocultural and Linguistic Anthropology",
                None,
                "24L/12T",
            ),
        }
        keys = {
            "code",
            "name",
            "campus",
            "description",
            "prerequisites",
            "exclusions",
            "enrolment_limits",
            "distribution_requirement",
            "instruction_hours",
            "delivery_mode",
            "program_area",
            "url",
        }
        for code, (name, prerequisite, hours) in expected.items():
            with self.subTest(code=code):
                course = parse_course_page((FIXTURES / f"{code}.html").read_text())
                self.assertEqual(set(course), keys)
                self.assertEqual(course["code"], code.upper())
                self.assertEqual(course["name"], name)
                self.assertEqual(course["campus"], "UTM")
                self.assertEqual(course["prerequisites"], prerequisite)
                self.assertEqual(course["instruction_hours"], hours)
                self.assertEqual(course["url"], course_url(code))
                for field in [
                    "description",
                    "exclusions",
                    "distribution_requirement",
                    "delivery_mode",
                    "program_area",
                ]:
                    self.assertTrue(course[field], field)
        course = parse_course_page((FIXTURES / "csc108h5.html").read_text())
        self.assertEqual(
            course["delivery_mode"], "Online; In Class; Hybrid; Online (Summer only)"
        )
        self.assertIsNone(course["enrolment_limits"])
        self.assertTrue(
            parse_course_page((FIXTURES / "csc207h5.html").read_text())[
                "enrolment_limits"
            ]
        )

    def test_invalid_titles(self):
        for title in [
            "",
            "<h1>CSC148H5 • Name</h1>",
            '<h1 class="page-title">CSC148H5</h1>',
            '<h1 class="page-title">CSC148H5 • </h1>',
            '<h1 class="page-title">invalid • Name</h1>',
        ]:
            with self.subTest(title=title), self.assertRaises(ValueError):
                parse_course_page(title)

    def test_description_without_label(self):
        course = parse_course_page(
            '<h1 class="page-title">CSC148H5 • Name • Detail</h1><div class="field field--name-field-desc"><div class="field__item"><p>First</p><p>Second</p></div></div>'
        )
        self.assertEqual(course["description"], "First Second")
        self.assertEqual(course["name"], "Name • Detail")
        self.assertIsNone(course["prerequisites"])

    def test_incomplete_course_body_is_rejected(self):
        for body in [
            "",
            '<div class="field field--name-field-desc"></div>',
            '<div class="field field--name-field-desc"><div class="field__item"> &nbsp; </div></div>',
            '<div class="field"><label class="field__label">Prerequisites</label><div class="field__item">CSC108H5</div></div>',
        ]:
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(ValueError, "description"),
            ):
                parse_course_page('<h1 class="page-title">CSC148H5 • Name</h1>' + body)

    def test_url_validation(self):
        self.assertEqual(
            course_url(" csc148h5 "), "https://utm.calendar.utoronto.ca/course/csc148h5"
        )
        for code in [
            "",
            "../course",
            "CSC148H1",
            "CSC148H5?x=1",
            "CSC١٤٨H5",
            "CSC１４８H5",
            "CſC148H5",
            "Cß148H5",
            None,
            148,
        ]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                course_url(code)

    @patch("crawler.utm_course.HttpClient")
    def test_fetch_contract(self, client_cls):
        client = client_cls.return_value.__enter__.return_value
        client.get_text.return_value = "html"
        self.assertEqual(fetch_course_page("CSC148H5"), "html")
        client.get_text.assert_called_once_with(course_url("CSC148H5"))
        client.get_text.side_effect = requests.HTTPError("404")
        with self.assertRaises(requests.HTTPError):
            fetch_course_page("CSC148H5")
        client.get_text.side_effect = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            fetch_course_page("CSC148H5")


if __name__ == "__main__":
    unittest.main()
