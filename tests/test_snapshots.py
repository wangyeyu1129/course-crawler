"""Historical export preservation and publication failure boundaries."""

import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from crawler.snapshots import archive_file
from crawler.storage import write_json_array_atomic, write_json_atomic


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "data/utm_courses.json"

    def test_first_export_and_unicode_round_trip(self):
        courses = [
            {"code": "CSC148H5", "description": "课程 • café", "prerequisites": None}
        ]
        snapshot = write_json_atomic(self.output, courses, preserve_history=True)
        self.assertEqual(snapshot.parent, self.root / "data/history/utm_courses")
        payload = self.output.read_bytes()
        self.assertEqual(snapshot.stem, hashlib.sha256(payload).hexdigest())
        self.assertEqual(snapshot.read_bytes(), payload)
        self.assertEqual(json.loads(snapshot.read_text()), courses)

    def test_changed_export_preserves_old_and_new(self):
        old = write_json_atomic(self.output, [{"name": "Old"}], preserve_history=True)
        old_bytes = old.read_bytes()
        new = write_json_atomic(self.output, [{"name": "New"}], preserve_history=True)
        self.assertNotEqual(old, new)
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertEqual(new.read_bytes(), self.output.read_bytes())
        self.assertEqual(len(list(old.parent.glob("*.json"))), 2)

    def test_preexisting_export_is_archived_before_first_update(self):
        self.output.parent.mkdir(parents=True)
        payload = b'[ {"name":"previous formatting"} ]\n'
        self.output.write_bytes(payload)
        snapshot = write_json_atomic(self.output, [], preserve_history=True)
        previous = snapshot.parent / f"{hashlib.sha256(payload).hexdigest()}.json"
        self.assertEqual(previous.read_bytes(), payload)
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_identical_export_reuses_snapshot(self):
        first = write_json_atomic(self.output, [], preserve_history=True)
        second = write_json_atomic(self.output, [], preserve_history=True)
        self.assertEqual(first, second)
        self.assertEqual(list(first.parent.glob("*.json")), [first])

    def test_editing_latest_cannot_mutate_history(self):
        snapshot = write_json_atomic(
            self.output, [{"name": "original"}], preserve_history=True
        )
        original = snapshot.read_bytes()
        self.output.write_text("edited in place")
        self.assertEqual(snapshot.read_bytes(), original)
        self.assertNotEqual(snapshot.stat().st_ino, self.output.stat().st_ino)

    def test_archive_failure_preserves_latest(self):
        write_json_atomic(self.output, ["old"])
        original = self.output.read_bytes()
        with patch("crawler.storage.archive_file", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_json_atomic(self.output, ["new"], preserve_history=True)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(list(self.output.parent.glob(".utm_courses.json.*")), [])

    def test_corrupt_snapshot_is_not_silently_overwritten(self):
        snapshot = write_json_atomic(self.output, ["old"], preserve_history=True)
        original = self.output.read_bytes()
        snapshot.write_text("corrupt")
        with self.assertRaisesRegex(OSError, "integrity"):
            write_json_atomic(self.output, ["new"], preserve_history=True)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(snapshot.read_text(), "corrupt")

    def test_failed_latest_replacement_keeps_both_archives(self):
        old = write_json_atomic(self.output, ["old"], preserve_history=True)
        original = self.output.read_bytes()
        with patch("crawler.storage.os.replace", side_effect=OSError("publish failed")):
            with self.assertRaises(OSError):
                write_json_atomic(self.output, ["new"], preserve_history=True)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(old.read_bytes(), original)
        self.assertEqual(len(list(old.parent.glob("*.json"))), 2)

    def test_archive_publication_failure_cleans_temporary_files(self):
        write_json_atomic(self.output, [])
        directory = self.root / "archive"
        with patch("crawler.snapshots.os.link", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                archive_file(self.output, directory)
        self.assertEqual(list(directory.iterdir()), [])

    def test_concurrent_identical_archive_is_safe(self):
        write_json_atomic(self.output, ["same"])
        real_link = os.link

        def publish_concurrently(source, target):
            real_link(source, target)
            raise FileExistsError()

        with patch("crawler.snapshots.os.link", side_effect=publish_concurrently):
            snapshot = archive_file(self.output, self.root / "archive")
        self.assertEqual(snapshot.read_bytes(), self.output.read_bytes())
        self.assertEqual(list(snapshot.parent.glob(".snapshot-*")), [])

    def test_serialization_failure_keeps_previous_export(self):
        write_json_atomic(self.output, ["old"])
        previous = self.output.read_bytes()
        with self.assertRaises(TypeError):
            write_json_atomic(self.output, [object()], preserve_history=True)
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertFalse((self.output.parent / "history").exists())

    def test_streaming_array_is_byte_compatible(self):
        for values in [
            [],
            [{}],
            [{"text": "课程", "optional": None}, {"nested": [1, {"line": "a\nb"}]}],
        ]:
            with self.subTest(values=values):
                write_json_array_atomic(self.output, iter(values))
                self.assertEqual(
                    self.output.read_text(),
                    json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                )

    def test_stream_failure_preserves_existing_export(self):
        write_json_atomic(self.output, ["old"])
        previous = self.output.read_bytes()

        def values():
            yield {"valid": True}
            raise RuntimeError("cursor failed")

        with self.assertRaises(RuntimeError):
            write_json_array_atomic(self.output, values(), preserve_history=True)
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertFalse((self.output.parent / "history").exists())
