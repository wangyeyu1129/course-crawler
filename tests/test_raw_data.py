"""Raw HTML history, integrity and offline input behavior."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from crawler.raw_data import RawArchive
from crawler.urls import course_url


class RawTests(unittest.TestCase):
    def test_history_deduplicates_bodies_and_tracks_latest_capture(self):
        with TemporaryDirectory() as directory, RawArchive(Path(directory)) as raw:
            url = course_url("CSC148H5")
            first = raw.capture(url, "<p>课程 A</p>", "one")
            raw.capture(url, "<p>B</p>", "two")
            again = raw.capture(url, "<p>课程 A</p>", "three")
            self.assertEqual(first, again)
            self.assertEqual(raw.latest(url), "<p>课程 A</p>")
            self.assertEqual(len(list(first.parent.glob("*.html"))), 2)
            self.assertEqual(raw.urls(), [url])

    def test_corrupt_or_missing_input_is_not_silently_used(self):
        with TemporaryDirectory() as directory, RawArchive(Path(directory)) as raw:
            with self.assertRaises(FileNotFoundError):
                raw.latest(course_url("CSC148H5"))
            path = raw.capture(course_url("CSC148H5"), "HTML", "run")
            path.write_text("corrupt")
            with self.assertRaisesRegex(OSError, "integrity"):
                raw.latest(course_url("CSC148H5"))
