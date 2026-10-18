import json
import logging
import tempfile
import unittest
from pathlib import Path

from crawler.logging_config import RunFormatter
from crawler.maintenance import gzip_digest, maintain_history
from crawler.snapshots import archive_file


class MaintenanceTests(unittest.TestCase):
    def test_plan_protects_current_and_apply_verifies_backup(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "courses.json"
            history = Path(root) / "history/courses"
            output.write_text("[1]")
            first = archive_file(output, history)
            output.write_text("[2]")
            second = archive_file(output, history)
            output.write_text("[3]")
            third = archive_file(output, history)
            output.write_text("[1]")
            self.assertEqual(maintain_history(output, keep=1), [str(second.resolve())])
            self.assertTrue(second.exists())
            maintain_history(output, keep=1, apply=True)
            self.assertFalse(second.exists())
            self.assertEqual(gzip_digest(second.with_suffix(".json.gz")), second.stem)
            self.assertTrue(first.exists())
            self.assertTrue(third.exists())
            self.assertEqual(maintain_history(output, keep=1, apply=True), [])

    def test_corrupt_backup_does_not_delete_source(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "courses.json"
            output.write_text("[1]")
            first = archive_file(output, Path(root) / "history/courses")
            output.write_text("[2]")
            archive_file(output, first.parent)
            first.with_suffix(".json.gz").write_bytes(b"corrupt")
            with self.assertRaises(OSError):
                maintain_history(output, keep=1, apply=True)
            self.assertTrue(first.exists())

    def test_invalid_retention(self):
        for keep in [0, -1, True]:
            with self.assertRaises(ValueError):
                maintain_history(Path("unused"), keep=keep)

    def test_json_log_identity_and_multiline_exception(self):
        record = logging.LogRecord(
            "crawler.http", logging.INFO, "", 1, "hello\n%s", ("world",), None
        )
        result = json.loads(
            RunFormatter(
                {"run_id": "run", "git_revision": "abc", "parser_version": "v"}
            ).format(record)
        )
        self.assertEqual(result["run_id"], "run")
        self.assertEqual(result["message"], "hello\nworld")
        self.assertEqual(result["logger"], "crawler.http")
