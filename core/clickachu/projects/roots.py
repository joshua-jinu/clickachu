"""Allowed roots (PLAN §8, §13.2): folders the agent may touch, optionally writable."""

import os
import sqlite3

from clickachu.api.protocol import AllowedRoot
from clickachu.db.conn import Database


class RootNotFound(Exception):
    pass


class RootExists(Exception):
    pass


def normalise(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def _row(row: sqlite3.Row) -> AllowedRoot:
    return AllowedRoot(id=row["id"], path=row["path"], can_write=bool(row["can_write"]))


class RootStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[AllowedRoot]:
        rows = self._db.conn().execute("SELECT * FROM allowed_roots ORDER BY path COLLATE NOCASE")
        return [_row(r) for r in rows.fetchall()]

    def get(self, root_id: int) -> AllowedRoot:
        row = (
            self._db.conn()
            .execute("SELECT * FROM allowed_roots WHERE id = ?", (root_id,))
            .fetchone()
        )
        if row is None:
            raise RootNotFound(f"allowed root {root_id} not found")
        return _row(row)

    def add(self, path: str, can_write: bool) -> AllowedRoot:
        norm = normalise(path)
        conn = self._db.conn()
        # Windows paths are case-insensitive; the UNIQUE column is not.
        if conn.execute(
            "SELECT 1 FROM allowed_roots WHERE path = ? COLLATE NOCASE", (norm,)
        ).fetchone():
            raise RootExists(f"{norm} is already allowed")
        with conn:
            cur = conn.execute(
                "INSERT INTO allowed_roots (path, can_write) VALUES (?, ?)", (norm, int(can_write))
            )
        return self.get(int(cur.lastrowid or 0))

    def set_write(self, root_id: int, can_write: bool) -> AllowedRoot:
        self.get(root_id)
        conn = self._db.conn()
        with conn:
            conn.execute(
                "UPDATE allowed_roots SET can_write = ? WHERE id = ?", (int(can_write), root_id)
            )
        return self.get(root_id)

    def remove(self, root_id: int) -> None:
        self.get(root_id)
        conn = self._db.conn()
        with conn:
            conn.execute("DELETE FROM allowed_roots WHERE id = ?", (root_id,))
