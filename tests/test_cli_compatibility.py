"""Exercise the public CLI in a fresh process using archived HTML only."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from crawler.raw_data import RawArchive
from crawler.utm_course import course_url, parse_course_page

ROOT = Path(__file__).resolve().parents[1]


class CliCompatibilityTests(unittest.TestCase):
    def test_reparse_resume_json_and_logging_remain_compatible(self):
        fixtures = ROOT / "tests/fixtures"
        expected = []
        with TemporaryDirectory() as directory:
            work = Path(directory)
            raw = work / "raw"
            with RawArchive(raw) as archive:
                for code in ("CSC148H5", "ANT102H5"):
                    html = (fixtures / (code.lower() + ".html")).read_text()
                    archive.capture(course_url(code), html, "compatibility-fixture")
                    expected.append(parse_course_page(html))
            args = [
                "main.py",
                "--crawl",
                "--database",
                str(work / "state.sqlite3"),
                "--output",
                str(work / "courses.json"),
                "--log-file",
                str(work / "run.log"),
                "--raw-directory",
                str(raw),
            ]
            # No accidental real network traffic in this end-to-end CLI check.
            script = (
                "import sys,requests; from main import main; "
                "requests.sessions.Session.request=lambda *a,**k: "
                '(_ for _ in ()).throw(AssertionError("unexpected HTTP")); '
                "sys.argv=sys.argv[1:]; raise SystemExit(main())"
            )

            def invoke(extra):
                result = subprocess.run(
                    [sys.executable, "-c", script, *args, *extra],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(
                    set(value),
                    {
                        "run_id",
                        "metadata",
                        "published",
                        "export_path",
                        "run",
                        "queue",
                        "snapshot",
                    },
                )
                return value

            partial = invoke(["--operation", "reparse", "--limit", "1"])
            self.assertFalse(partial["published"])
            self.assertEqual(
                partial["queue"], {"pending": 1, "succeeded": 1, "failed": 0}
            )
            self.assertFalse((work / "courses.json").exists())
            complete = invoke(["--operation", "reparse"])
            self.assertTrue(complete["published"])
            self.assertEqual(
                json.loads((work / "courses.json").read_text()),
                sorted(expected, key=lambda course: course["url"]),
            )
            resumed = invoke([])
            self.assertEqual(resumed["run"]["processed"], 0)
            for line in (work / "run.log").read_text().splitlines():
                record = json.loads(line)
                self.assertTrue(record["run_id"])
                self.assertTrue(record["parser_version"])
                if "crawl_progress" in record["message"]:
                    self.assertEqual(record["logger"], "crawler.pipeline")

    def test_inner_layers_import_without_loading_adapters_or_frameworks(self):
        script = (
            "import sys; import domain.course,application.crawl; "
            'assert not any(name.split(".")[0] in '
            '{"crawler","requests","bs4","sqlite3","api","infrastructure"} '
            "for name in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
