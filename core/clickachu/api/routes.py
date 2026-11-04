"""HTTP routes for memory, projects, allowed roots and the action log (PLAN §7, §14, §15)."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response

from clickachu.api.protocol import (
    ActionLogEntry,
    AllowedRoot,
    AllowedRootCreate,
    AllowedRootUpdate,
    ConversationDetail,
    ConversationMessage,
    ConversationSummary,
    Memory,
    MemoryCreate,
    MemoryExport,
    MemoryImportResult,
    MemoryScopeType,
    MemoryUpdate,
    Project,
    ProjectCreate,
    ProjectDetectRequest,
    ProjectProposal,
    ProjectUpdate,
)
from clickachu.db.conn import Database
from clickachu.memory.service import MemoryService
from clickachu.memory.store import MemoryNotFound, MemoryRefused, MemoryStoreError
from clickachu.projects.detect import detect
from clickachu.projects.profiles import ProjectNameTaken, ProjectNotFound, ProjectStore
from clickachu.projects.roots import RootExists, RootNotFound, RootStore

log = logging.getLogger(__name__)

LOG_LIMIT = 200


def project_path_memory(name: str, path: str) -> str:
    return f"Project {name} lives at {path}"


def create_memory_router(memory: MemoryService) -> APIRouter:
    router = APIRouter()
    store = memory.store

    def require_enabled() -> None:
        if not memory.enabled:
            raise HTTPException(409, "memory is turned off")

    @router.get("/memories")
    async def list_memories(
        scope_type: MemoryScopeType | None = None,
        scope_key: str | None = None,
        q: str | None = None,
        include_disabled: bool = True,
    ) -> list[Memory]:
        if not (q and q.strip()):
            return store.list(
                scope_type=scope_type, scope_key=scope_key, include_disabled=include_disabled
            )
        # The management list searches every scope, unlike prompt retrieval.
        scopes: tuple[MemoryScopeType, ...] = (
            (scope_type,) if scope_type else ("global", "project", "app")
        )
        hits: dict[int, Memory] = {}
        for stype in scopes:
            for m in store.search(
                q,
                scope_type=stype,
                scope_key=scope_key,
                limit=100,
                include_disabled=include_disabled,
            ):
                hits[m.id] = m
        return list(hits.values())

    @router.post("/memories", status_code=201)
    async def create_memory(body: MemoryCreate) -> Memory:
        require_enabled()
        try:
            return store.create(body)
        except MemoryRefused as exc:
            raise HTTPException(422, str(exc)) from exc

    # Registered before /memories/{id} so "export" is never parsed as an id.
    @router.get("/memories/export")
    async def export_memories() -> MemoryExport:
        return store.export_json()

    @router.post("/memories/import")
    async def import_memories(body: MemoryExport) -> MemoryImportResult:
        require_enabled()
        return store.import_json(body)

    @router.patch("/memories/{memory_id}")
    async def update_memory(memory_id: int, body: MemoryUpdate) -> Memory:
        try:
            return store.update(memory_id, body)
        except MemoryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except MemoryStoreError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/memories/{memory_id}", status_code=204)
    async def delete_memory(memory_id: int) -> Response:
        try:
            store.delete(memory_id)
        except MemoryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(status_code=204)

    return router


def create_projects_router(
    projects: ProjectStore, roots: RootStore, memory: MemoryService
) -> APIRouter:
    router = APIRouter()

    def sync_path_memory(project: Project, previous: Project | None = None) -> None:
        """Keep the project-scoped "lives at" memory in step with the profile (if memory is on)."""
        if not memory.enabled:
            return
        store = memory.store
        content = project_path_memory(project.name, project.path)
        try:
            if previous is not None:
                old_prefix = project_path_memory(previous.name, "")
                for m in store.list(scope_type="project", scope_key=previous.name):
                    if m.content.startswith(old_prefix):
                        store.update(m.id, MemoryUpdate(content=content, scope_key=project.name))
                        return
            if any(
                m.content == content
                for m in store.list(scope_type="project", scope_key=project.name)
            ):
                return
            store.create(
                MemoryCreate(
                    content=content,
                    scope_type="project",
                    scope_key=project.name,
                    category="fact",
                )
            )
        except MemoryStoreError as exc:
            log.warning("could not store project path memory: %s", exc)

    @router.get("/projects")
    async def list_projects() -> list[Project]:
        return projects.list()

    @router.post("/projects", status_code=201)
    async def create_project(body: ProjectCreate) -> Project:
        try:
            project = projects.create(body)
        except ProjectNameTaken as exc:
            raise HTTPException(409, str(exc)) from exc
        sync_path_memory(project)
        return project

    @router.post("/projects/detect")
    async def detect_project(body: ProjectDetectRequest) -> ProjectProposal:
        try:
            return await asyncio.to_thread(detect, Path(body.path))
        except (NotADirectoryError, OSError) as exc:
            raise HTTPException(400, f"not a folder: {body.path}") from exc

    @router.patch("/projects/{project_id}")
    async def update_project(project_id: int, body: ProjectUpdate) -> Project:
        try:
            previous = projects.get(project_id)
            project = projects.update(project_id, body)
        except ProjectNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except ProjectNameTaken as exc:
            raise HTTPException(409, str(exc)) from exc
        if (project.name, project.path) != (previous.name, previous.path):
            sync_path_memory(project, previous)
        return project

    @router.delete("/projects/{project_id}", status_code=204)
    async def delete_project(project_id: int) -> Response:
        try:
            projects.delete(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(status_code=204)

    @router.get("/allowed-roots")
    async def list_roots() -> list[AllowedRoot]:
        return roots.list()

    @router.post("/allowed-roots", status_code=201)
    async def add_root(body: AllowedRootCreate) -> AllowedRoot:
        try:
            return roots.add(body.path, body.can_write)
        except RootExists as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/allowed-roots/{root_id}")
    async def update_root(root_id: int, body: AllowedRootUpdate) -> AllowedRoot:
        try:
            return roots.set_write(root_id, body.can_write)
        except RootNotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.delete("/allowed-roots/{root_id}", status_code=204)
    async def remove_root(root_id: int) -> Response:
        try:
            roots.remove(root_id)
        except RootNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(status_code=204)

    return router


def create_log_router(db: Database) -> APIRouter:
    router = APIRouter()

    @router.get("/log")
    async def get_log(
        run_id: str | None = None, limit: int = Query(LOG_LIMIT, ge=1, le=1000)
    ) -> list[ActionLogEntry]:
        where, params = ("WHERE run_id = ?", (run_id,)) if run_id else ("", ())
        rows = db.conn().execute(
            f"SELECT * FROM action_log {where} ORDER BY id DESC LIMIT ?", (*params, limit)
        )
        return [
            ActionLogEntry(
                id=r["id"],
                run_id=r["run_id"],
                step_id=r["step_id"],
                tool=r["tool"],
                args_json=r["args_json"],
                tier=r["tier"],
                decision=r["decision"],
                ok=None if r["ok"] is None else bool(r["ok"]),
                result_summary=r["result_summary"],
                verified=None if r["verified"] is None else bool(r["verified"]),
                started_at=r["started_at"],
                duration_ms=r["duration_ms"],
            )
            for r in rows.fetchall()
        ]

    return router


def create_conversations_router(db: Database) -> APIRouter:
    router = APIRouter()

    @router.get("/conversations")
    async def list_conversations(limit: int = Query(50, ge=1, le=200)) -> list[ConversationSummary]:
        conn = db.conn()
        rows = conn.execute(
            """
            SELECT c.id, c.started_at,
                   COUNT(m.id) as message_count,
                   (SELECT content FROM messages WHERE conversation_id = c.id AND role = 'user' ORDER BY id ASC LIMIT 1) as title
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        summaries = []
        for r in rows:
            raw_title = r["title"] or ""
            title = (raw_title[:60] + "...") if len(raw_title) > 60 else raw_title
            summaries.append(
                ConversationSummary(
                    id=r["id"],
                    started_at=r["started_at"],
                    title=title or "New Chat",
                    message_count=r["message_count"] or 0,
                )
            )
        return summaries

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> ConversationDetail:
        conn = db.conn()
        c_row = conn.execute(
            "SELECT id, started_at FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not c_row:
            raise HTTPException(404, f"Conversation {conversation_id} not found")
        m_rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
        messages = [
            ConversationMessage(
                id=m["id"],
                role=m["role"],
                content=m["content"],
                created_at=m["created_at"],
            )
            for m in m_rows
        ]
        return ConversationDetail(
            id=c_row["id"],
            started_at=c_row["started_at"],
            messages=messages,
        )

    @router.delete("/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str) -> Response:
        conn = db.conn()
        with conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return Response(status_code=204)

    return router
