"""Transactional course queue and results; SQLite is the recovery authority."""

import json
import os
import sqlite3
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable, Optional

from crawler.discovery import normalize_course_url
from crawler.snapshots import archive_file


def write_json_atomic(
    path: Path, value, *, preserve_history: bool = False
) -> Optional[Path]:
    """Publish JSON atomically, optionally archiving old and new bytes first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        snapshot = None
        if preserve_history:
            directory = path.parent / "history" / path.stem
            if path.exists():
                archive_file(path, directory)
            snapshot = archive_file(temporary, directory)
        os.replace(temporary, path)
        return snapshot
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class CrawlStore:
    """Single-worker persistent queue; result and success status commit together."""

    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS courses (
                    url TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'succeeded', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK ((status = 'succeeded') = (result IS NOT NULL))
                )
            """)

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.connection.close()

    def enqueue(self, urls: Iterable[str]) -> None:
        # Normalize before the transaction: an invalid batch never partly enters.
        normalized = []
        for url in urls:
            canonical = normalize_course_url(url)
            if canonical is None:
                raise ValueError(f"Invalid UTM course URL: {url!r}")
            normalized.append((canonical,))
        with self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO courses(url) VALUES (?)",
                normalized,
            )

    def work_urls(self, limit=None) -> list[str]:
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be a positive integer.")
        # Snapshot prevents repeatedly selecting a failed URL in the same run.
        rows = self.connection.execute(
            "SELECT url FROM courses WHERE status != 'succeeded' ORDER BY rowid LIMIT ?",
            (-1 if limit is None else limit,),
        )
        return [row["url"] for row in rows]

    def save_success(self, url: str, result: dict) -> None:
        payload = json.dumps(result, ensure_ascii=False)
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE courses SET status='succeeded', result=?, error=NULL,
                   attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                (payload, url),
            )
            if cursor.rowcount != 1:
                raise ValueError("Cannot save an unknown course URL.")

    def save_failure(self, url: str, error: str) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE courses SET status='failed', result=NULL, error=?,
                   attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                (error, url),
            )
            if cursor.rowcount != 1:
                raise ValueError("Cannot fail an unknown course URL.")

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(("pending", "succeeded", "failed"), 0)
        counts.update(
            {
                row[0]: row[1]
                for row in self.connection.execute(
                    "SELECT status, COUNT(*) FROM courses GROUP BY status"
                )
            }
        )
        return counts

    def export(self, path: Path) -> Path:
        courses = [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT result FROM courses WHERE status='succeeded' ORDER BY url"
            )
        ]
        snapshot = write_json_atomic(path, courses, preserve_history=True)
        assert snapshot is not None
        return snapshot
