"""CLI discovery output and failure behavior."""

from collections import deque
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from main import main


class CliTests(unittest.TestCase):
    @patch('main.discover_course_urls', return_value=deque(['https://example.test/course']))
    def test_discovery_stdout(self, discover):
        output = StringIO()
        with patch('sys.argv', ['main.py', '--discover']), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue()), list(discover.return_value))

    @patch('main.discover_course_urls', return_value=deque(['https://example.test/course']))
    def test_discovery_output_file(self, discover):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            with patch('sys.argv', ['main.py', '--discover', '--output', str(path)]):
                main()
            self.assertEqual(json.loads(path.read_text()), list(discover.return_value))

    @patch('main.discover_course_urls', side_effect=ValueError('Incomplete catalog'))
    def test_failed_discovery_preserves_previous_queue(self, discover):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            path.write_text('["previous"]')
            with patch('sys.argv', ['main.py', '--discover', '--output', str(path)]):
                with self.assertRaises(ValueError):
                    main()
            self.assertEqual(path.read_text(), '["previous"]')
