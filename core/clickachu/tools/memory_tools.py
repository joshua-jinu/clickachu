"""Memory tools (PLAN §13.3, §14): `recall` (READ) and `remember` (LOW)."""

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from clickachu.api.protocol import (
    Memory,
    MemoryCategory,
    MemoryRef,
    MemorySavedEvent,
    MemoryScopeType,
)
from clickachu.db.conn import Database
from clickachu.llm.json_ask import make_ask_json
from clickachu.memory.dedup import Judge, Verdict, llm_judge
from clickachu.memory.remember import is_grounded_in_user_message, remember_from_message
from clickachu.memory.store import MemoryNotFound, MemoryStore, scope_label
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult, WorkerName


class RecallArgs(BaseModel):
    query: str = Field(min_length=1, description="Keywords or question to search memories for")
    scope: MemoryScopeType | None = Field(
        default=None, description="Optional scope filter ('global', 'project', or 'app')"
    )


class RememberArgs(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=2000,
        description="Concise fact, preference, or instruction stated by the user in this message",
    )
    category: MemoryCategory = Field(
        default="fact", description="'preference', 'instruction', or 'fact'"
    )
    scope_type: MemoryScopeType = Field(
        default="global", description="'global', 'project', or 'app'"
    )
    scope_key: str = Field(
        default="", description="Project name or process name when scope_type is not 'global'"
    )


class RecallTool(Tool[RecallArgs]):
    name: ClassVar[str] = "recall"
    description: ClassVar[str] = (
        "Search persistent user memories by keyword query and optional scope."
    )
    Args: ClassVar[type[BaseModel]] = RecallArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "io"

    def preview(self, args: RecallArgs) -> str:
        scope_part = f" (scope={args.scope})" if args.scope else ""
        return f"Recall memories for {args.query!r}{scope_part}"

    def run(self, args: RecallArgs, ctx: ToolContext) -> ToolResult:
        if ctx.memory is not None and not ctx.memory.enabled:
            return ToolResult(
                ok=True,
                data=[],
                summary="Memory is turned off",
                untrusted_text="Memory is turned off.",
            )
        if ctx.settings is not None and not ctx.settings.memory_enabled:
            return ToolResult(
                ok=True,
                data=[],
                summary="Memory is turned off",
                untrusted_text="Memory is turned off.",
            )

        store = ctx.memory.store if ctx.memory is not None else MemoryStore(ctx.db)
        project = ctx.snapshot.project.name if ctx.snapshot and ctx.snapshot.project else None
        process = ctx.snapshot.process if ctx.snapshot else None

        if args.scope is not None:
            hits = store.search(args.query, scope_type=args.scope, limit=8)
            if not hits:
                q_lower = args.query.lower()
                hits = [
                    m
                    for m in store.list(
                        scope_type=args.scope, include_disabled=False, limit=50
                    )
                    if q_lower in m.content.lower() or q_lower in m.scope_key.lower()
                ][:8]
        else:
            hits = store.search(args.query, project=project, app=process, limit=8)
            if not hits:
                q_lower = args.query.lower()
                hits = [
                    m
                    for m in store.recent(project=project, app=process, limit=20)
                    if q_lower in m.content.lower() or q_lower in m.scope_key.lower()
                ][:8]

        store.mark_used([m.id for m in hits])
        items = [
            {
                "id": m.id,
                "content": m.content,
                "category": m.category,
                "scope": scope_label(m),
            }
            for m in hits
        ]
        lines = [f"[{item['id']}] ({item['scope']}) {item['content']}" for item in items]
        noun = "memory" if len(items) == 1 else "memories"
        return ToolResult(
            ok=True,
            data=items,
            summary=f"Recalled {len(items)} {noun}",
            untrusted_text="\n".join(lines) if lines else "No matching memories found.",
        )


def _fallback_judge(existing: Memory, new_content: str) -> Verdict:
    if existing.content.strip().lower() == new_content.strip().lower():
        return "same"
    return "new"


class RememberTool(Tool[RememberArgs]):
    name: ClassVar[str] = "remember"
    description: ClassVar[str] = (
        "Save a durable preference, instruction, or fact explicitly stated by the user in their "
        "own message."
    )
    Args: ClassVar[type[BaseModel]] = RememberArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "io"

    def __init__(self) -> None:
        self._last_db: Database | None = None

    def preview(self, args: RememberArgs) -> str:
        return f"Remember ({args.category}): {args.content}"

    async def run(self, args: RememberArgs, ctx: ToolContext) -> ToolResult:
        self._last_db = ctx.db
        if (ctx.memory is not None and not ctx.memory.enabled) or (
            ctx.settings is not None and not ctx.settings.memory_enabled
        ):
            return ToolResult(ok=False, summary="Memory is turned off")

        if not is_grounded_in_user_message(args.content, ctx.user_text):
            return ToolResult(
                ok=False,
                summary="Refused: memory content is not grounded in the user's own message",
            )

        store = ctx.memory.store if ctx.memory is not None else MemoryStore(ctx.db)
        project = (
            args.scope_key.strip()
            if args.scope_type == "project" and args.scope_key.strip()
            else (ctx.snapshot.project.name if ctx.snapshot and ctx.snapshot.project else None)
        )
        process = (
            args.scope_key.strip()
            if args.scope_type == "app" and args.scope_key.strip()
            else (ctx.snapshot.process if ctx.snapshot else None)
        )
        app = ctx.snapshot.app if ctx.snapshot else None

        async def pre_extracted(_system: str, _user: str) -> dict[str, Any]:
            return {
                "content": args.content,
                "category": args.category,
                "scope": args.scope_type,
            }

        model = ""
        if ctx.settings is not None:
            s = ctx.settings
            model = s.active_fast_model

        if ctx.provider is not None and model:
            judge: Judge = llm_judge(make_ask_json(ctx.provider, model, max_tokens=60))
        else:

            async def simple_judge(existing: Memory, new_content: str) -> Verdict:
                return _fallback_judge(existing, new_content)

            judge = simple_judge

        outcome = await remember_from_message(
            store,
            pre_extracted,
            judge,
            ctx.user_text,
            project=project,
            process=process,
            app=app,
        )
        if outcome is None:
            return ToolResult(
                ok=False,
                summary="Refused: memory could not be saved (empty or looks like a secret)",
            )

        saved = outcome.memory
        if outcome.action != "duplicate" and ctx.emit is not None:
            await ctx.emit(
                MemorySavedEvent(
                    memory=MemoryRef(
                        id=saved.id, content=saved.content, scope=scope_label(saved)
                    )
                )
            )

        return ToolResult(
            ok=True,
            data={"id": saved.id, "content": saved.content, "action": outcome.action},
            summary=f"Saved memory [{saved.id}]: {saved.content}",
        )

    def verify(self, args: RememberArgs, result: ToolResult) -> bool | None:
        del args
        if not result.ok or not isinstance(result.data, dict):
            return None
        mem_id = result.data.get("id")
        if not isinstance(mem_id, int) or self._last_db is None:
            return False
        try:
            MemoryStore(self._last_db).get(mem_id)
            return True
        except MemoryNotFound:
            return False
