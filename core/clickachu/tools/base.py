"""Tool base types and execution context (PLAN §13.1)."""

import asyncio
import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from clickachu.api.protocol import ContextSnapshot, ServerMessage, Settings
from clickachu.automation.com_worker import run_com as default_run_com
from clickachu.db.conn import Database
from clickachu.llm.provider import LLMProvider
from clickachu.memory.service import MemoryService

WorkerName = Literal["com", "browser", "io"]
WorkerRunner = Callable[..., Awaitable[Any]]
Emit = Callable[[ServerMessage], Awaitable[None]]


class Tier(StrEnum):
    READ = "read"
    LOW = "low"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    summary: str = ""
    untrusted_text: str | None = None

    def llm_text(self) -> str:
        """Text payload to wrap inside `<untrusted_data source="tool:...">` (§12)."""
        if self.untrusted_text is not None:
            if self.summary and not self.untrusted_text.startswith(self.summary):
                return f"{self.summary}\n{self.untrusted_text}"
            return self.untrusted_text
        if self.data is not None:
            return json.dumps(
                {"ok": self.ok, "summary": self.summary, "data": self.data},
                ensure_ascii=False,
                default=str,
            )
        return self.summary or ("ok" if self.ok else "failed")


async def _default_run_io(fn: Callable[..., Any], *args: Any, timeout: float = 30.0) -> Any:
    if inspect.iscoroutinefunction(fn):
        return await asyncio.wait_for(fn(*args), timeout=timeout)
    return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)


async def _default_run_browser(
    fn: Callable[..., Any], *args: Any, timeout: float = 30.0
) -> Any:
    from clickachu.automation.browser_worker import run_browser

    return await run_browser(fn, *args, timeout=timeout)


async def _missing_browser_worker(
    _fn: Callable[..., Any], *_args: Any, timeout: float = 30.0
) -> Any:
    del timeout
    raise RuntimeError("browser worker is not available")


@dataclass(frozen=True)
class ToolWorkers:
    """Dispatches tool calls to the right worker thread/loop (PLAN §3 Hard Rule 6, §13.1)."""

    com: WorkerRunner = default_run_com
    browser: WorkerRunner = _default_run_browser
    io: WorkerRunner = _default_run_io

    async def run(
        self, worker: WorkerName, fn: Callable[..., Any], *args: Any, timeout: float = 30.0
    ) -> Any:
        if inspect.iscoroutinefunction(fn) and worker == "io":
            return await asyncio.wait_for(fn(*args), timeout=timeout)
        runner = {"com": self.com, "browser": self.browser, "io": self.io}[worker]
        result = await runner(fn, *args, timeout=timeout)
        if inspect.isawaitable(result):
            return await asyncio.wait_for(result, timeout=timeout)
        return result


@dataclass
class ToolContext:
    run_id: str
    step_id: str
    snapshot: ContextSnapshot | None
    db: Database
    workers: ToolWorkers = field(default_factory=ToolWorkers)
    user_text: str = ""
    memory: MemoryService | None = None
    settings: Settings | None = None
    provider: LLMProvider | None = None
    emit: Emit | None = None
    # Processes started during this run that have not been verified yet; killed on cancel (§12).
    unverified_cleanup: list[Callable[[], None]] = field(default_factory=list)


class Tool[ArgsT: BaseModel](ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]
    tier: ClassVar[Tier]
    worker: ClassVar[WorkerName] = "io"
    timeout_s: ClassVar[float] = 30.0
    writes_files: ClassVar[bool] = False

    @abstractmethod
    def run(self, args: ArgsT, ctx: ToolContext) -> ToolResult | Awaitable[ToolResult]: ...

    def verify(self, args: ArgsT, result: ToolResult) -> bool | None:
        del args, result
        return None

    def preview(self, args: ArgsT) -> str:
        """Human-readable summary for the step timeline and approval card (§9.3, §13.1)."""
        fields = args.model_dump(exclude_none=True)
        if not fields:
            return self.name
        parts = [f"{k}={v!r}" for k, v in fields.items()]
        return f"{self.name}({', '.join(parts)})"

    def trust_key(self, args: ArgsT) -> str | None:
        """Key stored in `trusted_actions` when the user chooses 'Always' (§13.1)."""
        del args
        return None

    def read_paths(self, args: ArgsT) -> Sequence[str]:
        """Paths that must reside inside an allowed root (§13.2)."""
        if self.writes_files:
            return ()
        return _extract_paths(args, ("path", "root", "src"))

    def write_paths(self, args: ArgsT) -> Sequence[str]:
        """Paths that must reside inside a writable (`can_write=1`) allowed root (§13.2)."""
        if self.writes_files:
            return _extract_paths(args, ("path", "src", "dst", "paths"))
        return _extract_paths(args, ("dst", "paths"))

    def target_urls(self, args: ArgsT) -> Sequence[str]:
        """URLs or domains inspected against the sensitive-domain list (§13.2)."""
        return _extract_paths(args, ("url", "domain"))


def _extract_paths(args: BaseModel, names: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    data = args.model_dump()
    for name in names:
        val = data.get(name)
        if isinstance(val, str) and val.strip():
            found.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    found.append(item)
    return found
