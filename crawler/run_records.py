"""Run metadata kept separately from the course JSON array."""

import hashlib
import logging
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from crawler.storage import write_json_atomic


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def code_identity() -> dict:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in ("utm_course.py", "fields.py", "urls.py", "models.py"):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root.parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = None
    return {"git_revision": revision, "parser_version": digest.hexdigest()}


class RunRecord:
    def __init__(self, database: Path, output: Path, operation: str):
        self.database = Path(database)
        self.run_id = uuid4().hex
        self.path = (
            Path(output).parent / "runs" / Path(output).stem / (self.run_id + ".json")
        )
        self.data = {
            "run_id": self.run_id,
            "started_at": utc_now(),
            "finished_at": None,
            "operation": operation,
            "status": "running",
            "database": str(self.database.resolve()),
            "output": str(Path(output).resolve()),
            "snapshot": None,
            "snapshot_sha256": None,
            "queue": None,
            "complete": False,
            "published": False,
            **code_identity(),
        }

    def __enter__(self):
        write_json_atomic(self.path, self.data)
        return self

    def snapshot(self, path: Path):
        self.data["snapshot"] = str(Path(path).resolve())
        self.data["snapshot_sha256"] = Path(path).stem

    def __exit__(self, exc_type, error, traceback):
        self.data["finished_at"] = utc_now()
        try:
            with sqlite3.connect(
                self.database.resolve().as_uri() + "?mode=ro", uri=True
            ) as connection:
                self.data["database_schema_version"] = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                counts = dict.fromkeys(("pending", "succeeded", "failed"), 0)
                counts.update(
                    dict(
                        connection.execute(
                            "SELECT status,COUNT(*) FROM courses GROUP BY status"
                        )
                    )
                )
                self.data["queue"] = counts
                self.data["complete"] = (
                    bool(sum(counts.values()))
                    and counts["pending"] == 0
                    and counts["failed"] == 0
                )
        except sqlite3.Error:
            self.data["complete"] = False
        self.data["status"] = (
            ("interrupted" if isinstance(error, KeyboardInterrupt) else "failed")
            if error is not None
            else ("completed" if self.data["complete"] else "partial")
        )
        if error is not None:
            self.data["error"] = {"type": type(error).__name__, "message": str(error)}
        try:
            write_json_atomic(self.path, self.data)
        except Exception:
            if error is None:
                raise
            logging.getLogger(__name__).exception(
                "run_metadata_write_failed run_id=%s", self.run_id
            )
