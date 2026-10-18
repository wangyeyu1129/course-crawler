"""CLI discovery output and failure behavior."""

import json
import unittest
from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from main import main


class CliTests(unittest.TestCase):
    @patch(
        "main.discover_course_urls", return_value=deque(["https://example.test/course"])
    )
    def test_discovery_stdout(self, discover):
        output = StringIO()
        with patch("sys.argv", ["main.py", "--discover"]), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue()), list(discover.return_value))

    @patch(
        "main.discover_course_urls", return_value=deque(["https://example.test/course"])
    )
    def test_discovery_output_file(self, discover):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            with patch("sys.argv", ["main.py", "--discover", "--output", str(path)]):
                main()
            self.assertEqual(json.loads(path.read_text()), list(discover.return_value))

    @patch("main.discover_course_urls", side_effect=ValueError("Incomplete catalog"))
    def test_failed_discovery_preserves_previous_queue(self, discover):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            path.write_text('["previous"]')
            with patch("sys.argv", ["main.py", "--discover", "--output", str(path)]):
                with self.assertRaises(ValueError):
                    main()
            self.assertEqual(path.read_text(), '["previous"]')

    @patch("main.run_crawl")
    def test_crawl_policy_flags_and_exit_status(self, crawl):
        crawl.return_value = {"queue": {"failed": 1}}
        with (
            patch(
                "sys.argv",
                [
                    "main.py",
                    "--crawl",
                    "--interval",
                    "0.5",
                    "--timeout",
                    "12",
                    "--retries",
                    "2",
                    "--user-agent",
                    "test-bot",
                ],
            ),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(main(), 1)
        policy = crawl.call_args.kwargs["policy"]
        self.assertEqual(
            (policy.interval, policy.read_timeout, policy.retries, policy.user_agent),
            (0.5, 12, 2, "test-bot"),
        )
        crawl.return_value = {"queue": {"failed": 0}}
        with patch("sys.argv", ["main.py", "--crawl"]), redirect_stdout(StringIO()):
            self.assertEqual(main(), 0)

    @patch("main.run_crawl")
    @patch("main.HttpClient")
    def test_invalid_configuration_stops_before_io(self, client, crawl):
        for args in [
            ["--user-agent", " invalid"],
            ["--user-agent", "中文"],
            ["--timeout", "0"],
            ["--interval", "nan"],
            ["--max-count-drop", "nan"],
            ["--max-count-drop", "2"],
            ["--limit", "0"],
        ]:
            with (
                self.subTest(args=args),
                patch("sys.argv", ["main.py", "--crawl", *args]),
                redirect_stderr(StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    main()
                self.assertEqual(caught.exception.code, 2)
        client.assert_not_called()
        crawl.assert_not_called()

    @patch(
        "main.discover_course_urls", return_value=deque(["https://example.test/course"])
    )
    def test_file_output_does_not_build_an_unused_json_string(self, discover):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "queue.json"
            with (
                patch("sys.argv", ["main.py", "--discover", "--output", str(output)]),
                patch(
                    "main.json.dumps",
                    side_effect=AssertionError("unused serialization"),
                ),
            ):
                self.assertEqual(main(), 0)
            self.assertEqual(
                json.loads(output.read_text()), list(discover.return_value)
            )

    @patch("main.run_crawl")
    @patch("main.HttpClient")
    @patch("main.fetch_course_page")
    def test_inapplicable_options_fail_before_io(self, fetch, client, crawl):
        cases = [
            ["--limit", "1"],
            ["--discover", "--workers", "1"],
            ["--discover", "CSC148H5"],
            ["--crawl", "CSC148H5"],
            ["--crawl", "--operation", "discover", "--limit=1"],
            ["--crawl", "--operation", "reparse", "--retries", "3"],
            ["--crawl", "--operation", "reparse", "--url-file", "urls.json"],
            ["--crawl", "--operation", "refresh", "--url-file", "urls.json"],
            ["--crawl", "--operation", "resume", "--refresh"],
            ["--crawl", "--wor", "2"],
        ]
        for args in cases:
            with (
                self.subTest(args=args),
                patch("sys.argv", ["main.py", *args]),
                redirect_stderr(StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    main()
                self.assertEqual(caught.exception.code, 2)
        fetch.assert_not_called()
        client.assert_not_called()
        crawl.assert_not_called()

    @patch("main.run_crawl", return_value={"queue": {"failed": 0}})
    def test_offline_and_legacy_refresh_are_accepted(self, crawl):
        for args in [["--operation", "reparse"], ["--refresh"]]:
            with (
                patch("sys.argv", ["main.py", "--crawl", *args]),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(main(), 0)
