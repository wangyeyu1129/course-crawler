"""Immutable HTML inputs and a persistent URL/run capture index."""

import sqlite3
import threading
from pathlib import Path

from crawler.durability import durable_mkdir, temporary_file
from crawler.migrations import execute_script, migrate_database
from crawler.snapshots import archive_file, file_digest
from crawler.urls import normalize_course_url
from infrastructure.clock import utc_now


class RawArchive:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        durable_mkdir(self.directory)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            self.directory / "index.sqlite3", check_same_thread=False
        )

        def schema(connection):
            execute_script(
                connection,
                """CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY,url TEXT NOT NULL,digest TEXT NOT NULL,
                run_id TEXT NOT NULL,captured_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS captures_url ON captures(url,id);""",
            )

        try:
            migrate_database(self.connection, (schema,), 0x52415748, {"captures"})
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def capture(self, url: str, html: str, run_id: str) -> Path:
        if normalize_course_url(url) != url or not isinstance(html, str):
            raise ValueError(
                "Raw input must have a canonical course URL and HTML text."
            )
        with temporary_file(self.directory, text=True) as (handle, temporary):
            handle.write(html)
            handle.flush()
            target = archive_file(temporary, self.directory / "html", suffix=".html")
            with self._lock, self.connection:
                self.connection.execute(
                    "INSERT INTO captures(url,digest,run_id,captured_at) VALUES (?,?,?,?)",
                    (url, target.stem, run_id, utc_now()),
                )
            return target

    def latest(self, url: str) -> str:
        with self._lock:
            row = self.connection.execute(
                "SELECT digest FROM captures WHERE url=? ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"No archived HTML for {url}")
        path = self.directory / "html" / (row[0] + ".html")
        if file_digest(path) != row[0]:
            raise OSError(f"Raw HTML integrity check failed: {url}")
        return path.read_text(encoding="utf-8")

    def urls(self):
        with self._lock:
            return [
                row[0]
                for row in self.connection.execute(
                    "SELECT url FROM captures GROUP BY url ORDER BY MIN(id)"
                )
            ]
