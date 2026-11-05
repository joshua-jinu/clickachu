"""Explicit file transfer between allowed host folders and isolated task workspaces."""

import shutil
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from clickachu.context.redact import redact
from clickachu.projects.roots import RootStore
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult
from clickachu.tools.execution import task_workspace
from clickachu.tools.policy import check_path_allowed, resolve_safe_path

MAX_TRANSFER_BYTES = 50 * 1024 * 1024


class WorkspaceFilesArgs(BaseModel):
    operation: Literal["list", "read", "import", "export"]
    name: str = Field(default="", max_length=200, description="Single workspace filename")
    path: str | None = Field(
        default=None, description="Allowed host source/import or destination/export"
    )


class WorkspaceFilesTool(Tool[WorkspaceFilesArgs]):
    name: ClassVar[str] = "workspace_files"
    description: ClassVar[str] = (
        "List/read task artifacts, import a file from an allowed host folder into isolated "
        "/workspace, or export an artifact to an allowed writable host path after approval. "
        "Imports/exports never overwrite existing files. Maximum transfer size is 50 MB."
    )
    Args: ClassVar[type[BaseModel]] = WorkspaceFilesArgs
    tier: ClassVar[Tier] = Tier.CONFIRM

    def read_paths(self, args):
        return (args.path,) if args.operation == "import" and args.path else ()

    def write_paths(self, args):
        return (args.path,) if args.operation == "export" and args.path else ()

    def run(self, args: WorkspaceFilesArgs, ctx: ToolContext) -> ToolResult:
        workspace = task_workspace(ctx)
        if args.operation == "list":
            entries = []
            for entry in workspace.iterdir():
                if len(entries) >= 100:
                    break
                if entry.is_file() and not entry.is_symlink():
                    entries.append({"name": entry.name, "bytes": entry.stat().st_size})
            return ToolResult(ok=True, summary="Listed task workspace.", data=entries)
        if (
            not args.name
            or args.name in (".", "..")
            or any(character in args.name for character in ("/", "\\", ":", "\x00"))
        ):
            return ToolResult(
                ok=False, summary="Use a single workspace filename, without directories."
            )
        artifact = workspace / args.name
        if artifact.is_symlink() or artifact.resolve().parent != workspace:
            return ToolResult(ok=False, summary="Artifact escapes the task workspace.")
        if args.operation == "read":
            with artifact.open("rb") as source:
                data = source.read(16_001)
            content = data[:16_000].decode("utf-8", errors="replace")
            return ToolResult(
                ok=True,
                summary=f"Read {args.name}",
                untrusted_text=redact(content + ("\n[truncated]" if len(data) > 16_000 else "")),
            )
        if not args.path:
            return ToolResult(ok=False, summary="An allowed host path is required.")
        if error := check_path_allowed(
            args.path, RootStore(ctx.db).list(), require_write=args.operation == "export"
        ):
            return ToolResult(ok=False, summary=error)
        host = resolve_safe_path(args.path, RootStore(ctx.db).list())
        if host is None:
            return ToolResult(ok=False, summary="Host path is invalid.")
        source, destination = (host, artifact) if args.operation == "import" else (artifact, host)
        if not source.is_file() or source.stat().st_size > MAX_TRANSFER_BYTES:
            return ToolResult(ok=False, summary="Source must be a file no larger than 50 MB.")
        # Exclusive create makes an export approval specific to a new artifact, not replacement.
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=64 * 1024)
        return ToolResult(
            ok=True,
            summary=f"{args.operation.title()}ed {args.name}.",
            data={"path": str(destination), "bytes": destination.stat().st_size},
        )
