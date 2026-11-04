"""SQLite access (PLAN §8): one connection per thread, schema applied idempotently."""

import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer Clickachu."""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._ready = False

    def conn(self) -> sqlite3.Connection:
        """This thread's connection, opened (and the schema applied) on first use."""
        existing: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        conn = self._open()
        self._local.conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error as exc:
                    log.warning("closing sqlite connection failed: %s", exc)
            self._connections.clear()
            self._ready = False
        self._local = threading.local()

    def _open(self) -> sqlite3.Connection:
        # check_same_thread=False only so close() may run from another thread; each connection
        # is still used by the thread that opened it.
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            if not self._ready:
                self._apply_schema(conn)
                self._ready = True
            self._connections.append(conn)
        return conn

    def _apply_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        current = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
        if current is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()
        elif current > SCHEMA_VERSION:
            raise SchemaTooNewError(
                f"database schema v{current} is newer than this build (v{SCHEMA_VERSION})"
            )
        elif current < SCHEMA_VERSION:
            # Future migrations hook in here, one step per version.
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
            conn.commit()
