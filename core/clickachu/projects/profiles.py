"""Project profiles CRUD (PLAN §8, §15) and window-title matching for context capture (§10)."""

import json
import re
import sqlite3
from datetime import UTC, datetime

from clickachu.api.protocol import Project, ProjectCommand, ProjectCreate, ProjectRef, ProjectUpdate
from clickachu.db.conn import Database

# " — ", " – " (VS Code on macOS/Linux, PLAN example) and " - " (VS Code on Windows).
_TITLE_SEPARATOR = re.compile(r"\s+[—–-]\s+")


class ProjectNotFound(Exception):
    pass


class ProjectNameTaken(Exception):
    pass


def _row(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        commands=[ProjectCommand.model_validate(c) for c in json.loads(row["commands_json"])],
        env=json.loads(row["env_json"]),
        created_at=row["created_at"],
        approved_at=row["approved_at"],
    )


def _folder_name(path: str) -> str:
    return re.split(r"[\\/]+", path.rstrip("\\/"))[-1]


def _dump_commands(commands: list[ProjectCommand]) -> str:
    return json.dumps([c.model_dump() for c in commands])


class ProjectStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[Project]:
        rows = self._db.conn().execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")
        return [_row(r) for r in rows.fetchall()]

    def get(self, project_id: int) -> Project:
        row = (
            self._db.conn().execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        )
        if row is None:
            raise ProjectNotFound(f"project {project_id} not found")
        return _row(row)

    def get_by_name(self, name: str) -> Project | None:
        row = (
            self._db.conn()
            .execute("SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name.strip(),))
            .fetchone()
        )
        return _row(row) if row is not None else None

    def create(self, data: ProjectCreate) -> Project:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn = self._db.conn()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO projects (name, path, commands_json, env_json, created_at,"
                    " approved_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        data.name.strip(),
                        data.path,
                        _dump_commands(data.commands),
                        json.dumps(data.env),
                        now,
                        # The user reviewed the proposal before saving it (§15).
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ProjectNameTaken(f"a project named {data.name!r} already exists") from exc
        return self.get(int(cur.lastrowid or 0))

    def update(self, project_id: int, patch: ProjectUpdate) -> Project:
        current = self.get(project_id)
        merged = ProjectCreate(
            name=patch.name or current.name,
            path=patch.path or current.path,
            commands=current.commands if patch.commands is None else patch.commands,
            env=current.env if patch.env is None else patch.env,
        )
        conn = self._db.conn()
        try:
            with conn:
                conn.execute(
                    "UPDATE projects SET name = ?, path = ?, commands_json = ?, env_json = ?,"
                    " approved_at = ? WHERE id = ?",
                    (
                        merged.name.strip(),
                        merged.path,
                        _dump_commands(merged.commands),
                        json.dumps(merged.env),
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        project_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ProjectNameTaken(f"a project named {merged.name!r} already exists") from exc
        return self.get(project_id)

    def delete(self, project_id: int) -> None:
        self.get(project_id)
        conn = self._db.conn()
        with conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # ------------------------------------------------------------ context matching

    def match(self, window_title: str, process: str) -> ProjectRef | None:
        """The project whose name or folder shows in the window title (longest/strongest wins)."""
        if not window_title:
            return None
        title = window_title.casefold()
        segments = {s.strip() for s in _TITLE_SEPARATOR.split(title)}
        best: tuple[int, int, Project] | None = None
        for project in self.list():
            score = _match_score(project, title, segments)
            if score and (best is None or (score, len(project.name)) > best[:2]):
                best = (score, len(project.name), project)
        return ProjectRef(id=best[2].id, name=best[2].name) if best else None


def _match_score(project: Project, title: str, segments: set[str]) -> int:
    """2 = a whole title segment (VS Code "file — folder — app"), 1 = word in the title."""
    names = {project.name.casefold(), _folder_name(project.path).casefold()} - {""}
    if names & segments:
        return 2
    for name in names:
        if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", title):
            return 1
    return 0
