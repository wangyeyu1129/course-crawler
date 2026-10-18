"""CLI discovery output and failure behavior."""

import json
import unittest
from collections import deque
from contextlib import redirect_stdout
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
