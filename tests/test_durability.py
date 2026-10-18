import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.durability import cleanup_orphans, temporary_file
from crawler.storage import write_json_atomic


class DurabilityTests(unittest.TestCase):
    def test_publication_syncs_parent_after_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "output.json"
            calls = []
            replace = os.replace

            def tracked(source, destination):
                replace(source, destination)
                calls.append("replace")

            with (
                patch("crawler.storage.os.replace", side_effect=tracked),
                patch(
                    "crawler.storage.sync_directory",
                    side_effect=lambda p: calls.append("sync"),
                ),
            ):
                write_json_atomic(target, [])
            self.assertEqual(calls, ["replace", "sync"])

    def test_normal_temporary_cleanup_and_active_process_protection(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            with temporary_file(directory) as (handle, path):
                handle.write(b"active")
                self.assertEqual(cleanup_orphans(directory, min_age=0, apply=True), [])
                self.assertTrue(path.exists())
            self.assertEqual(list(directory.iterdir()), [])

    def test_killed_process_orphan_is_planned_then_removed(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    'import os,sys; from pathlib import Path; from crawler.durability import temporary_file;\nwith temporary_file(Path(sys.argv[1])) as (h,p):\n h.write(b"interrupted");h.flush();os._exit(0)',
                    root,
                ],
                check=True,
            )
            paths = list(Path(root).iterdir())
            self.assertEqual(len(paths), 1)
            self.assertEqual(cleanup_orphans(Path(root)), [])
            self.assertEqual(cleanup_orphans(Path(root), min_age=0), [str(paths[0])])
            self.assertTrue(paths[0].exists())
            cleanup_orphans(Path(root), min_age=0, apply=True)
            self.assertFalse(paths[0].exists())

    def test_symlink_unknown_name_and_locked_file_are_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            real = directory / "user.tmp"
            real.write_text("keep")
            link = directory / ".crawler-tmp-99999999-symlink"
            link.symlink_to(real)
            with (
                temporary_file(directory) as (handle, path),
                patch("crawler.durability.os.kill", side_effect=ProcessLookupError),
            ):
                self.assertEqual(cleanup_orphans(directory, min_age=0, apply=True), [])
                self.assertTrue(path.exists())
            self.assertTrue(link.is_symlink())
            self.assertEqual(real.read_text(), "keep")

    def test_cleanup_failure_preserves_primary_error(self):
        with tempfile.TemporaryDirectory() as root:
            with (
                patch("crawler.durability.sync_directory", side_effect=OSError("sync")),
                self.assertLogs("crawler.durability", level="ERROR"),
            ):
                with self.assertRaisesRegex(ValueError, "primary"):
                    with temporary_file(Path(root)):
                        raise ValueError("primary")
