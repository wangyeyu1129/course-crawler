import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from crawler.http_cache import HttpCache
from crawler.migrations import migrate_database
from crawler.storage import CrawlStore


class MigrationTests(unittest.TestCase):
    def test_failed_migration_rolls_back_ddl_and_version(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)

        def broken(database):
            database.execute("CREATE TABLE partial (value TEXT)")
            raise RuntimeError("migration failed")

        with self.assertRaises(RuntimeError):
            migrate_database(connection, (broken,), 123, set())
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0],
            0,
        )

    def test_versioned_open_is_idempotent_and_newer_versions_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            with CrawlStore(path) as store:
                self.assertEqual(
                    store.connection.execute("PRAGMA user_version").fetchone()[0], 1
                )
            with CrawlStore(path) as store:
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations"
                    ).fetchone()[0],
                    1,
                )
                store.connection.execute("PRAGMA user_version=99")
            with self.assertRaisesRegex(ValueError, "newer"):
                CrawlStore(path)
            with sqlite3.connect(path) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0], 99
                )

    def test_database_component_cannot_be_confused(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            with HttpCache(path):
                pass
            with self.assertRaisesRegex(ValueError, "different application"):
                CrawlStore(path)
