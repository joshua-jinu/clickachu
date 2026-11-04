"""Memory store (PLAN §8, §14): SQLite rows + FTS5 retrieval, scope filters, supersede, JSON IO."""

import re
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from clickachu.api.protocol import (
    Memory,
    MemoryCreate,
    MemoryExport,
    MemoryImportResult,
    MemoryUpdate,
)
from clickachu.context.redact import contains_secret
from clickachu.db.conn import Database

LIST_LIMIT = 1000
Memories = list[Memory]  # `list` is shadowed by MemoryStore.list inside the class body

_TOKEN = re.compile(r"\w+", re.UNICODE)
# Function words that would only add noise to a free-text FTS query.
STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from how i if in is it its me my of on or "
    "our should so that the their then there these this to us was we what when where which "
    "who why will with would you your".split()
)


class MemoryStoreError(Exception):
    """Base class for store failures."""


class MemoryRefused(MemoryStoreError):
    """The content looks like a secret (matches a redaction pattern, §14)."""


class MemoryNotFound(MemoryStoreError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def tokens(text: str) -> list[str]:
    """Lower-cased content words, stopwords and 1-char tokens dropped, order kept, deduped."""
    seen: dict[str, None] = {}
    for tok in _TOKEN.findall(text.lower()):
        if len(tok) > 1 and tok not in STOPWORDS:
            seen.setdefault(tok)
    return list(seen)


def fts_query(text: str) -> str | None:
    toks = tokens(text)
    if not toks:
        return None
    # Quoted so FTS5 syntax characters / keywords in user text (AND, NEAR, "-") stay literal.
    return " OR ".join(f'"{t}"' for t in toks)


def scope_label(memory: Memory) -> str:
    return memory.scope_type if not memory.scope_key else f"{memory.scope_type}:{memory.scope_key}"


def _normalise_scope(scope_type: str, scope_key: str) -> tuple[str, str]:
    key = scope_key.strip()
    if scope_type == "global":
        return scope_type, ""
    if not key:
        raise MemoryRefused(f"a {scope_type} memory needs a scope_key")
    return scope_type, key


def _row(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row["id"],
        scope_type=row["scope_type"],
        scope_key=row["scope_key"],
        category=row["category"],
        content=row["content"],
        pinned=bool(row["pinned"]),
        enabled=bool(row["enabled"]),
        use_count=row["use_count"],
        superseded_by=row["superseded_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_used_at=row["last_used_at"],
    )


# Matches rows visible from a context: global + this project + this app (names case-insensitive).
_IN_SCOPE = (
    "(m.scope_type = 'global'"
    " OR (m.scope_type = 'project' AND m.scope_key = :project COLLATE NOCASE)"
    " OR (m.scope_type = 'app' AND m.scope_key = :app COLLATE NOCASE))"
)
_ACTIVE = "m.enabled = 1 AND m.superseded_by IS NULL"


class MemoryStore:
    def __init__(self, db: Database, clock: Callable[[], str] = now_iso) -> None:
        self._db = db
        self._now = clock

    # ------------------------------------------------------------ CRUD

    def create(self, data: MemoryCreate, *, source_message_id: int | None = None) -> Memory:
        content = self._checked(data.content)
        scope_type, scope_key = _normalise_scope(data.scope_type, data.scope_key)
        now = self._now()
        conn = self._db.conn()
        with conn:
            cur = conn.execute(
                "INSERT INTO memories (scope_type, scope_key, category, content,"
                " source_message_id, pinned, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scope_type,
                    scope_key,
                    data.category,
                    content,
                    source_message_id,
                    int(data.pinned),
                    now,
                    now,
                ),
            )
        return self.get(int(cur.lastrowid or 0))

    def get(self, memory_id: int) -> Memory:
        row = (
            self._db.conn()
            .execute("SELECT * FROM memories m WHERE m.id = ?", (memory_id,))
            .fetchone()
        )
        if row is None:
            raise MemoryNotFound(f"memory {memory_id} not found")
        return _row(row)

    def update(self, memory_id: int, patch: MemoryUpdate) -> Memory:
        current = self.get(memory_id)
        fields = patch.model_dump(exclude_none=True)
        if not fields:
            return current
        if "content" in fields:
            fields["content"] = self._checked(fields["content"])
        scope_type, scope_key = _normalise_scope(
            fields.get("scope_type", current.scope_type),
            fields.get("scope_key", current.scope_key),
        )
        fields["scope_type"], fields["scope_key"] = scope_type, scope_key
        for flag in ("pinned", "enabled"):
            if flag in fields:
                fields[flag] = int(fields[flag])
        fields["updated_at"] = self._now()
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        conn = self._db.conn()
        with conn:
            conn.execute(
                f"UPDATE memories SET {assignments} WHERE id = :id", {**fields, "id": memory_id}
            )
        return self.get(memory_id)

    def delete(self, memory_id: int) -> None:
        self.get(memory_id)
        conn = self._db.conn()
        with conn:
            # Deleting a replacement brings back whatever it superseded (also makes Undo whole).
            conn.execute(
                "UPDATE memories SET superseded_by = NULL WHERE superseded_by = ?", (memory_id,)
            )
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def list(
        self,
        *,
        scope_type: str | None = None,
        scope_key: str | None = None,
        include_disabled: bool = True,
        include_superseded: bool = False,
        limit: int = LIST_LIMIT,
    ) -> Memories:
        where, params = self._filters(scope_type, scope_key, include_disabled, include_superseded)
        rows = (
            self._db.conn()
            .execute(
                f"SELECT m.* FROM memories m {where}"
                " ORDER BY m.pinned DESC, m.updated_at DESC, m.id DESC LIMIT :limit",
                {**params, "limit": limit},
            )
            .fetchall()
        )
        return [_row(r) for r in rows]

    # ------------------------------------------------------------ retrieval

    def search(
        self,
        query: str,
        *,
        project: str | None = None,
        app: str | None = None,
        scope_type: str | None = None,
        scope_key: str | None = None,
        limit: int = 8,
        include_disabled: bool = False,
    ) -> Memories:
        """FTS5 bm25-ranked hits, best first; never returns superseded rows.

        Pass `project`/`app` to see global + those scopes (retrieval), or `scope_type`/`scope_key`
        for one exact scope (dedup, UI filter). Disabled rows are excluded unless asked for.
        """
        match = fts_query(query)
        if match is None:
            return []
        where, params = self._filters(scope_type, scope_key, include_disabled, False)
        if scope_type is None:
            where += " AND " + _IN_SCOPE
            params.update(project=project or "", app=app or "")
        rows = (
            self._db.conn()
            .execute(
                "SELECT m.* FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid"
                f" {where} AND memories_fts MATCH :match"
                " ORDER BY bm25(memories_fts, 1.0, 0.5) LIMIT :limit",
                {**params, "match": match, "limit": limit},
            )
            .fetchall()
        )
        return [_row(r) for r in rows]

    def pinned(self, *, project: str | None = None, app: str | None = None) -> Memories:
        return self._in_scope("m.pinned = 1", project, app, "m.updated_at DESC", 50)

    def recent(
        self, *, project: str | None = None, app: str | None = None, limit: int = 8
    ) -> Memories:
        return self._in_scope("1 = 1", project, app, "m.updated_at DESC, m.id DESC", limit)

    def mark_used(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        now = self._now()
        conn = self._db.conn()
        with conn:
            conn.executemany(
                "UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
                [(now, i) for i in ids],
            )

    def supersede(self, old_id: int, new_id: int) -> None:
        if old_id == new_id:
            raise MemoryStoreError("a memory cannot supersede itself")
        self.get(old_id)
        self.get(new_id)
        conn = self._db.conn()
        with conn:
            conn.execute(
                "UPDATE memories SET superseded_by = ?, updated_at = ? WHERE id = ?",
                (new_id, self._now(), old_id),
            )

    # ------------------------------------------------------------ JSON export / import

    def export_json(self) -> MemoryExport:
        rows = self.list(include_disabled=True, include_superseded=False)
        return MemoryExport(
            memories=[
                MemoryCreate(
                    content=m.content,
                    scope_type=m.scope_type,
                    scope_key=m.scope_key,
                    category=m.category,
                    pinned=m.pinned,
                )
                for m in sorted(rows, key=lambda m: m.id)
            ]
        )

    def import_json(self, payload: MemoryExport) -> MemoryImportResult:
        """Add memories not already present (same scope + content); secrets are skipped."""
        imported = skipped = 0
        for item in payload.memories:
            try:
                scope_type, scope_key = _normalise_scope(item.scope_type, item.scope_key)
                if self._exists(scope_type, scope_key, item.content):
                    skipped += 1
                    continue
                self.create(item)
            except MemoryRefused:
                skipped += 1
            else:
                imported += 1
        return MemoryImportResult(imported=imported, skipped=skipped)

    # ------------------------------------------------------------ internals

    @staticmethod
    def _checked(content: str) -> str:
        text = content.strip()
        if not text:
            raise MemoryRefused("memory content is empty")
        if contains_secret(text):
            raise MemoryRefused("that looks like a secret, so it was not saved")
        return text

    def _exists(self, scope_type: str, scope_key: str, content: str) -> bool:
        row = (
            self._db.conn()
            .execute(
                "SELECT 1 FROM memories WHERE scope_type = ? AND scope_key = ? COLLATE NOCASE"
                " AND content = ? AND superseded_by IS NULL",
                (scope_type, scope_key, content.strip()),
            )
            .fetchone()
        )
        return row is not None

    def _in_scope(
        self, extra: str, project: str | None, app: str | None, order: str, limit: int
    ) -> Memories:
        rows = (
            self._db.conn()
            .execute(
                f"SELECT m.* FROM memories m WHERE {_ACTIVE} AND {extra} AND {_IN_SCOPE}"
                f" ORDER BY {order} LIMIT :limit",
                {"project": project or "", "app": app or "", "limit": limit},
            )
            .fetchall()
        )
        return [_row(r) for r in rows]

    @staticmethod
    def _filters(
        scope_type: str | None,
        scope_key: str | None,
        include_disabled: bool,
        include_superseded: bool,
    ) -> tuple[str, dict[str, object]]:
        clauses = ["1 = 1"]
        params: dict[str, object] = {}
        if scope_type is not None:
            clauses.append("m.scope_type = :scope_type")
            params["scope_type"] = scope_type
        if scope_key is not None:
            clauses.append("m.scope_key = :scope_key COLLATE NOCASE")
            params["scope_key"] = scope_key
        if not include_disabled:
            clauses.append("m.enabled = 1")
        if not include_superseded:
            clauses.append("m.superseded_by IS NULL")
        return "WHERE " + " AND ".join(clauses), params
