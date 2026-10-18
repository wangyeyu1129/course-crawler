"""Mutation checks prove dependency contracts reject actual boundary violations."""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


class ArchitectureTests(unittest.TestCase):
    def test_dependency_contracts_accept_valid_tree_and_reject_mutations(self):
        cases = [
            ("valid", {}, True),
            ("domain_reverse", {"domain/broken.py": "import crawler.urls\n"}, False),
            (
                "application_relative",
                {
                    "application/broken.py": "from . import bridge\n",
                    "application/bridge.py": "from crawler.storage import CrawlStore\n",
                },
                False,
            ),
            ("network_in_domain", {"domain/broken.py": "import requests\n"}, False),
            (
                "system_reverse",
                {
                    "infrastructure/broken.py": "from application.models import WorkQueue\n"
                },
                False,
            ),
            ("api_adapter", {"api/broken.py": "import crawler.runner\n"}, False),
            (
                "cycle",
                {
                    "application/first.py": "from . import second\n",
                    "application/second.py": "from . import first\n",
                },
                False,
            ),
        ]
        for name, changes, valid in cases:
            with self.subTest(case=name), TemporaryDirectory() as directory:
                target = Path(directory)
                for package in (
                    "domain",
                    "application",
                    "infrastructure",
                    "crawler",
                    "api",
                ):
                    shutil.copytree(
                        ROOT / package,
                        target / package,
                        ignore=shutil.ignore_patterns("__pycache__"),
                    )
                shutil.copy(ROOT / ".importlinter", target / ".importlinter")
                for filename, content in changes.items():
                    (target / filename).write_text(content)
                # Same interpreter as the suite, without relying on a shell PATH.
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from importlinter.cli import lint_imports_command; lint_imports_command()",
                        "--no-cache",
                        "--no-logo",
                    ],
                    cwd=target,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    result.returncode, 0 if valid else 1, result.stdout + result.stderr
                )
                if not valid:
                    self.assertIn("BROKEN", result.stdout)
