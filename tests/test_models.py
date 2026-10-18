import unittest

from course_factory import course_record

from crawler.models import validate_course
from crawler.urls import course_url


class ModelTests(unittest.TestCase):
    def test_valid_record_is_copied_and_optional_none_is_preserved(self):
        original = course_record(course_url("CSC148H5"))
        validated = validate_course(original)
        self.assertEqual(original, validated)
        self.assertIsNot(original, validated)
        self.assertIsNone(validated["prerequisites"])

    def test_invalid_shape_types_identity_and_required_text(self):
        url = course_url("CSC148H5")
        for changes in [
            {"name": ""},
            {"campus": "UTSC"},
            {"description": None},
            {"code": "csc148h5"},
            {"prerequisites": []},
            {"url": course_url("CSC108H5")},
            {"extra": "unknown"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_course(course_record(url, **changes))
        missing = course_record(url)
        del missing["prerequisites"]
        with self.assertRaises(ValueError):
            validate_course(missing)
