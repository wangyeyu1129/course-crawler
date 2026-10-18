"""Numbered, transactional migrations for course, HTTP-cache and raw-index databases."""

import sqlite3


def execute_script(connection, script):
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("Incomplete migration SQL.")


def migrate_database(connection, migrations, application_id, allowed_tables):
    try:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        identity = connection.execute("PRAGMA application_id").fetchone()[0]
        if identity not in (0, application_id):
            raise ValueError("Database belongs to a different application component.")
        if version > len(migrations):
            raise ValueError("Database schema is newer than this application.")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if version == 0 and not tables <= (set(allowed_tables) | {"schema_migrations"}):
            raise ValueError("Unrecognized legacy database tables.")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for number in range(version + 1, len(migrations) + 1):
            migrations[number - 1](connection)
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)", (number,)
            )
            connection.execute(f"PRAGMA user_version={number}")
        connection.execute(f"PRAGMA application_id={application_id}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def course_schema(connection):
    connection.execute("""
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

    schema = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='courses'"
    ).fetchone()[0]
    if "CHECK ((status = 'succeeded') = (result IS NOT NULL))" in schema:
        connection.execute("""CREATE TABLE courses_updated (
            url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','succeeded','failed')), attempts INTEGER NOT NULL DEFAULT 0,
            result TEXT,error TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(status!='succeeded' OR result IS NOT NULL))""")
        connection.execute(
            "INSERT INTO courses_updated(rowid,url,status,attempts,result,error,updated_at) SELECT rowid,url,status,attempts,result,error,updated_at FROM courses"
        )
        connection.execute("DROP TABLE courses")
        connection.execute("ALTER TABLE courses_updated RENAME TO courses")
    execute_script(
        connection,
        """
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
    """,
    )


def migrate_courses(connection):
    migrate_database(
        connection,
        (course_schema,),
        0x43525345,
        {"courses", "dataset_version", "export_cache"},
    )
