"""SQLite connection factory, PRAGMA configuration, and schema migration runner."""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    """SQLite database manager enforcing WAL, foreign keys, and migrations."""

    def __init__(self, db_path: Path | str, auto_migrate: bool = True) -> None:
        """Initialize database with a file path and ensure initial migrations run."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if auto_migrate:
            self.apply_migrations()

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        """Apply required PRAGMAs to each SQLite connection."""
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        cursor.close()

    def _apply_migrations_to_conn(
        self,
        conn: sqlite3.Connection,
        migrations_dir: Path | None = None,
    ) -> None:
        """Execute all unapplied migrations directly on an active connection."""
        target_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
        if not target_dir.is_dir():
            return

        migration_files = sorted(target_dir.glob("*.sql"))
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        cursor.execute("SELECT filename FROM schema_migrations;")
        applied = {row[0] for row in cursor.fetchall()}

        for file in migration_files:
            if file.name not in applied:
                sql_script = file.read_text(encoding="utf-8")
                cursor.executescript(sql_script)
                now_utc = datetime.datetime.now(datetime.UTC).isoformat()
                cursor.execute(
                    "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?);",
                    (file.name, now_utc),
                )
        cursor.close()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding a configured SQLite connection with auto-migration."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,  # Manual transaction management
        )
        self._configure_connection(conn)

        # Ensure schema migrations table and base schema exist on connection
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations';"
        )
        has_migrations = cursor.fetchone() is not None
        cursor.close()
        if not has_migrations:
            self._apply_migrations_to_conn(conn)

        try:
            yield conn
        finally:
            conn.close()

    def apply_migrations(self, migrations_dir: Path | None = None) -> None:
        """Discover and execute all unapplied SQL migrations in numerical order."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._configure_connection(conn)
        try:
            self._apply_migrations_to_conn(conn, migrations_dir)
        finally:
            conn.close()
