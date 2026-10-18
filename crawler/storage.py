"""Transactional course queue and results; SQLite is the recovery authority."""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Iterable, Iterator, Optional, TextIO

from crawler.models import Course, validate_course
from crawler.snapshots import archive_file, file_digest
from crawler.urls import normalize_course_url


def _publish_json(
    path: Path, writer: Callable[[TextIO], None], *, preserve_history: bool = False
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
            writer(handle)
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


def write_json_atomic(
    path: Path, value, *, preserve_history: bool = False
) -> Optional[Path]:
    return _publish_json(
        path,
        lambda handle: json.dump(value, handle, ensure_ascii=False, indent=2),
        preserve_history=preserve_history,
    )


def write_json_array_atomic(
    path: Path, values: Iterable, *, preserve_history: bool = False
) -> Optional[Path]:
    """Stream an iterable with the same bytes as json.dumps(list, indent=2)."""

    def write(handle: TextIO) -> None:
        first = True
        for value in values:
            handle.write("[\n" if first else ",\n")
            encoded = json.dumps(value, ensure_ascii=False, indent=2)
            handle.write("  " + encoded.replace("\n", "\n  "))
            first = False
        handle.write("[]" if first else "\n]")

    return _publish_json(path, write, preserve_history=preserve_history)


@dataclass(frozen=True)
class WorkQueue:
    total: int
    urls: Iterator[str]


class CrawlStore:
    """Single-worker persistent queue; result and success status commit together."""

    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._batch_size = 1
        self._pending_writes = 0
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
                    CHECK (status != 'succeeded' OR result IS NOT NULL)
                )
            """)

            schema = self.connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='courses'"
            ).fetchone()[0]
            if "CHECK ((status = 'succeeded') = (result IS NOT NULL))" in schema:
                self.connection.execute("""CREATE TABLE courses_updated (
                    url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','succeeded','failed')), attempts INTEGER NOT NULL DEFAULT 0,
                    result TEXT,error TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK(status!='succeeded' OR result IS NOT NULL))""")
                self.connection.execute(
                    "INSERT INTO courses_updated(rowid,url,status,attempts,result,error,updated_at) SELECT rowid,url,status,attempts,result,error,updated_at FROM courses"
                )
                self.connection.execute("DROP TABLE courses")
                self.connection.execute("ALTER TABLE courses_updated RENAME TO courses")
            self.connection.executescript("""
                CREATE INDEX IF NOT EXISTS courses_status ON courses(status);
                CREATE TABLE IF NOT EXISTS dataset_version (
                    id INTEGER PRIMARY KEY CHECK (id=1), revision INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO dataset_version VALUES (1,0);
                CREATE TABLE IF NOT EXISTS export_cache (
                    path TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    digest TEXT NOT NULL, format_version INTEGER NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS result_insert AFTER INSERT ON courses
                WHEN NEW.result IS NOT NULL BEGIN
                    UPDATE dataset_version SET revision=revision+1 WHERE id=1;
                END;
                CREATE TRIGGER IF NOT EXISTS result_update AFTER UPDATE OF result ON courses
                WHEN OLD.result IS NOT NEW.result BEGIN
                    UPDATE dataset_version SET revision=revision+1 WHERE id=1;
                END;
                CREATE TRIGGER IF NOT EXISTS result_delete AFTER DELETE ON courses
                WHEN OLD.result IS NOT NULL BEGIN
                    UPDATE dataset_version SET revision=revision+1 WHERE id=1;
                END;
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

    @contextmanager
    def work_queue(
        self, limit=None, *, retry_failed: bool = False, batch_size: int = 100
    ):
        """Freeze run membership in SQLite, fetching only one bounded batch at a time."""
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be a positive integer.")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer.")
        with self.connection:
            self.connection.execute(
                "CREATE TEMP TABLE IF NOT EXISTS run_queue (sequence INTEGER PRIMARY KEY, url TEXT NOT NULL)"
            )
            self.connection.execute("DELETE FROM run_queue")
            selected = 0
            for status in ("pending", "failed") if retry_failed else ("pending",):
                remaining = -1 if limit is None else limit - selected
                if remaining == 0:
                    break
                self.connection.execute(
                    "INSERT INTO run_queue(url) SELECT url FROM courses WHERE status=? ORDER BY rowid LIMIT ?",
                    (status, remaining),
                )
                selected = self.connection.execute(
                    "SELECT COUNT(*) FROM run_queue"
                ).fetchone()[0]
        total = self.connection.execute("SELECT COUNT(*) FROM run_queue").fetchone()[0]
        cursor = self.connection.execute("SELECT url FROM run_queue ORDER BY sequence")

        def urls():
            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    return
                for row in batch:
                    yield row["url"]

        try:
            yield WorkQueue(total, urls())
        finally:
            cursor.close()

    def work_urls(self, limit=None, *, retry_failed: bool = False) -> list[str]:
        """Compatibility helper; the pipeline uses work_queue for bounded memory."""
        with self.work_queue(limit, retry_failed=retry_failed) as queue:
            return list(queue.urls)

    @contextmanager
    def batch_writes(self, size: int = 1):
        if type(size) is not int or size < 1:
            raise ValueError("commit_batch_size must be a positive integer.")
        if self._batch_size != 1 or self._pending_writes:
            raise RuntimeError("Nested result batches are not supported.")
        self._batch_size = size
        try:
            yield
        except BaseException as error:
            try:
                if isinstance(error, KeyboardInterrupt):
                    self.connection.commit()
                else:
                    self.connection.rollback()
            except Exception:
                self.connection.rollback()
                logging.getLogger(__name__).exception("batch_cleanup_failed")
            raise
        else:
            try:
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
        finally:
            self._batch_size = 1
            self._pending_writes = 0

    @contextmanager
    def _result_write(self):
        try:
            yield
            self._pending_writes += 1
            if self._pending_writes >= self._batch_size:
                self.connection.commit()
                self._pending_writes = 0
        except BaseException:
            self.connection.rollback()
            self._pending_writes = 0
            raise

    def save_success(self, url: str, result: Course) -> None:
        payload = json.dumps(
            validate_course(result, expected_url=url), ensure_ascii=False
        )
        with self._result_write():
            cursor = self.connection.execute(
                """UPDATE courses SET status='succeeded', result=?, error=NULL,
                   attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                (payload, url),
            )
            if cursor.rowcount != 1:
                raise ValueError("Cannot save an unknown course URL.")

    def save_failure(self, url: str, error: str) -> None:
        with self._result_write():
            cursor = self.connection.execute(
                """UPDATE courses SET status='failed', error=?,
                   attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                (error, url),
            )
            if cursor.rowcount != 1:
                raise ValueError("Cannot fail an unknown course URL.")

    def schedule_refresh(self):
        """Refresh the known queue without discarding last-known-good results."""
        with self.connection:
            self.connection.execute("UPDATE courses SET status='pending', error=NULL")

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
        path = Path(path)
        revision = self.connection.execute(
            "SELECT revision FROM dataset_version WHERE id=1"
        ).fetchone()[0]
        cached = self.connection.execute(
            "SELECT * FROM export_cache WHERE path=? AND format_version=1",
            (str(path.resolve()),),
        ).fetchone()
        if (
            cached is not None
            and cached["revision"] == revision
            and path.is_file()
            and file_digest(path) == cached["digest"]
        ):
            snapshot = (
                path.parent / "history" / path.stem / (cached["digest"] + ".json")
            )
            if snapshot.exists():
                if file_digest(snapshot) != cached["digest"]:
                    raise OSError(f"Snapshot integrity check failed: {snapshot}")
            else:
                snapshot = archive_file(path, snapshot.parent)
            return snapshot
        courses = (
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT result FROM courses WHERE result IS NOT NULL ORDER BY url"
            )
        )
        snapshot = write_json_array_atomic(path, courses, preserve_history=True)
        assert snapshot is not None
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO export_cache VALUES (?,?,?,1)",
                (str(path.resolve()), revision, snapshot.stem),
            )
        return snapshot
