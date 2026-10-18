"""Persistent, variant-aware HTTP bodies for conditional GET revalidation."""

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CachedPage:
    body: str
    etag: Optional[str]
    last_modified: Optional[str]


class HttpCache:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        with self.connection:
            self.connection.execute("""CREATE TABLE IF NOT EXISTS http_pages (
                url TEXT NOT NULL, variant TEXT NOT NULL, body TEXT NOT NULL,
                digest TEXT NOT NULL, etag TEXT, last_modified TEXT,
                PRIMARY KEY(url,variant))""")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def get(self, url: str, variant: str) -> Optional[CachedPage]:
        with self._lock:
            row = self.connection.execute(
                "SELECT body,digest,etag,last_modified FROM http_pages WHERE url=? AND variant=?",
                (url, variant),
            ).fetchone()
            if row is None:
                return None
            if hashlib.sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
                self.remove(url, variant)
                return None
            return CachedPage(row[0], row[2], row[3])

    def put(self, url: str, variant: str, page: CachedPage):
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO http_pages VALUES (?,?,?,?,?,?)",
                (
                    url,
                    variant,
                    page.body,
                    hashlib.sha256(page.body.encode("utf-8")).hexdigest(),
                    page.etag,
                    page.last_modified,
                ),
            )

    def remove(self, url: str, variant: str):
        with self._lock, self.connection:
            self.connection.execute(
                "DELETE FROM http_pages WHERE url=? AND variant=?", (url, variant)
            )
