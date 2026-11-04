"""File tools (PLAN §13.3): search, read, write, open, move, folder creation, and trash."""

import difflib
import fnmatch
import hashlib
import os
import shutil
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import send2trash
from pydantic import BaseModel, Field

from clickachu.projects.roots import RootStore
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult, WorkerName
from clickachu.tools.policy import normalise_safe_path

SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
MAX_SEARCH_LIMIT = 20


class SearchFilesArgs(BaseModel):
    query: str = Field(
        min_length=1, description="Filename substring or glob pattern to search for"
    )
    ext: str | None = Field(
        default=None, description="Optional file extension filter (e.g. 'pdf' or '.pdf')"
    )
    root: str | None = Field(
        default=None, description="Optional folder path inside allowed roots to search within"
    )
    limit: int = Field(
        default=MAX_SEARCH_LIMIT,
        ge=1,
        le=MAX_SEARCH_LIMIT,
        description="Maximum number of results to return (1..20)",
    )


def _matches_name(filename: str, query: str) -> bool:
    name_lower = filename.lower()
    stem_lower = Path(filename).stem.lower()
    q_lower = query.strip().lower()
    if not q_lower:
        return False
    if "*" in q_lower or "?" in q_lower:
        return fnmatch.fnmatch(name_lower, q_lower) or fnmatch.fnmatch(stem_lower, q_lower)
    if q_lower in name_lower:
        return True
    parts = q_lower.split()
    return len(parts) > 1 and all(part in name_lower for part in parts)


def _normalise_ext(ext: str | None) -> str | None:
    if not ext or not ext.strip():
        return None
    cleaned = ext.strip().lower().lstrip("*.")
    return f".{cleaned}" if cleaned else None


class SearchFilesTool(Tool[SearchFilesArgs]):
    name: ClassVar[str] = "search_files"
    description: ClassVar[str] = (
        "Search allowed folders for files matching a name query (and optional extension), "
        "sorted newest first (max 20 results)."
    )
    Args: ClassVar[type[BaseModel]] = SearchFilesArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "io"

    def preview(self, args: SearchFilesArgs) -> str:
        ext_part = f" (*{_normalise_ext(args.ext)})" if _normalise_ext(args.ext) else ""
        root_part = f" in {args.root}" if args.root else ""
        return f"Search files matching {args.query!r}{ext_part}{root_part}"

    def run(self, args: SearchFilesArgs, ctx: ToolContext) -> ToolResult:
        if args.root:
            resolved = normalise_safe_path(args.root)
            search_dirs = [resolved] if resolved is not None else []
        else:
            roots = RootStore(ctx.db).list()
            if not roots:
                return ToolResult(
                    ok=True,
                    data=[],
                    summary="No allowed folders configured in Settings",
                    untrusted_text="No allowed folders are configured in Settings.",
                )
            search_dirs = [
                p for r in roots if (p := normalise_safe_path(r.path)) is not None
            ]

        ext_filter = _normalise_ext(args.ext)
        cap = min(args.limit, MAX_SEARCH_LIMIT)
        candidates: list[tuple[float, Path, int]] = []
        seen: set[str] = set()

        for base in search_dirs:
            if not base.exists() or not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
                for fname in filenames:
                    if not _matches_name(fname, args.query):
                        continue
                    fpath = Path(dirpath) / fname
                    if ext_filter and fpath.suffix.lower() != ext_filter:
                        continue
                    norm_key = os.path.normcase(str(fpath))
                    if norm_key in seen:
                        continue
                    try:
                        stat = fpath.stat()
                    except OSError:
                        continue
                    seen.add(norm_key)
                    candidates.append((stat.st_mtime, fpath, stat.st_size))

        # Sort newest first; break ties deterministically by path.
        candidates.sort(key=lambda item: (-item[0], str(item[1]).lower()))
        top = candidates[:cap]

        items = [
            {
                "path": str(fpath),
                "name": fpath.name,
                "size_bytes": size,
                "modified_at": datetime.fromtimestamp(mtime, tz=UTC).isoformat(
                    timespec="seconds"
                ),
            }
            for mtime, fpath, size in top
        ]
        lines = [f"{item['path']} ({item['modified_at']})" for item in items]
        summary = f"Found {len(items)} file(s) matching {args.query!r}"
        return ToolResult(
            ok=True,
            data=items,
            summary=summary,
            untrusted_text="\n".join(lines) if lines else "No matching files found.",
        )


# ---------------------------------------------------------------- read_file


class ReadFileArgs(BaseModel):
    path: str = Field(min_length=1, description="Path of the file to read")
    max_bytes: int = Field(
        default=65536,
        ge=1,
        le=65536,
        description="Maximum bytes to read (default 64k, max 64k)",
    )


class ReadFileTool(Tool[ReadFileArgs]):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = "Read the contents of a file (up to 64k bytes)."
    Args: ClassVar[type[BaseModel]] = ReadFileArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "io"

    def preview(self, args: ReadFileArgs) -> str:
        return f"Read file {args.path}"

    def run(self, args: ReadFileArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        fpath = Path(args.path)
        if not fpath.exists():
            return ToolResult(ok=False, summary=f"File not found: {args.path}")
        if not fpath.is_file():
            return ToolResult(ok=False, summary=f"Not a file: {args.path}")

        try:
            raw = fpath.read_bytes()[: args.max_bytes]
            text = raw.decode("utf-8", errors="replace")
            summary = f"Read {len(raw)} bytes from {fpath.name}"
            return ToolResult(
                ok=True,
                data={"path": str(fpath), "bytes_read": len(raw)},
                summary=summary,
                untrusted_text=text,
            )
        except OSError as exc:
            return ToolResult(ok=False, summary=f"Could not read file {args.path}: {exc}")


# ---------------------------------------------------------------- open_file


class OpenFileArgs(BaseModel):
    path: str = Field(min_length=1, description="Path of the file to open")


class OpenFileTool(Tool[OpenFileArgs]):
    name: ClassVar[str] = "open_file"
    description: ClassVar[str] = "Open a file using its default associated Windows application."
    Args: ClassVar[type[BaseModel]] = OpenFileArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "io"

    def preview(self, args: OpenFileArgs) -> str:
        return f"Open file {args.path}"

    def run(self, args: OpenFileArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        fpath = Path(args.path)
        if not fpath.exists():
            return ToolResult(ok=False, summary=f"File not found: {args.path}")

        try:
            if sys.platform == "win32":
                os.startfile(str(fpath))
            else:
                webbrowser.open(str(fpath))
            return ToolResult(ok=True, summary=f"Opened file {fpath.name}")
        except OSError as exc:
            return ToolResult(ok=False, summary=f"Failed to open file {args.path}: {exc}")


# ---------------------------------------------------------------- open_url


class OpenUrlArgs(BaseModel):
    url: str = Field(min_length=1, description="Web URL to open in default browser")


class OpenUrlTool(Tool[OpenUrlArgs]):
    name: ClassVar[str] = "open_url"
    description: ClassVar[str] = "Open a web URL in the user's default browser."
    Args: ClassVar[type[BaseModel]] = OpenUrlArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "io"

    def preview(self, args: OpenUrlArgs) -> str:
        return f"Open URL {args.url}"

    def run(self, args: OpenUrlArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        try:
            if sys.platform == "win32":
                os.startfile(args.url)
            else:
                webbrowser.open(args.url)
            return ToolResult(ok=True, summary=f"Opened URL {args.url}")
        except OSError as exc:
            return ToolResult(ok=False, summary=f"Failed to open URL {args.url}: {exc}")


# ---------------------------------------------------------------- create_folder


class CreateFolderArgs(BaseModel):
    path: str = Field(min_length=1, description="Directory path to create")


class CreateFolderTool(Tool[CreateFolderArgs]):
    name: ClassVar[str] = "create_folder"
    description: ClassVar[str] = "Create a directory inside allowed folders (including parents)."
    Args: ClassVar[type[BaseModel]] = CreateFolderArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "io"
    writes_files: ClassVar[bool] = True

    def preview(self, args: CreateFolderArgs) -> str:
        return f"Create folder {args.path}"

    def run(self, args: CreateFolderArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        fpath = Path(args.path)
        try:
            fpath.mkdir(parents=True, exist_ok=True)
            return ToolResult(ok=True, summary=f"Created folder {args.path}")
        except OSError as exc:
            return ToolResult(ok=False, summary=f"Failed to create folder {args.path}: {exc}")

    def verify(self, args: CreateFolderArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        return Path(args.path).is_dir()


# ---------------------------------------------------------------- move_file


class MoveFileArgs(BaseModel):
    src: str = Field(min_length=1, description="Source file or folder path")
    dst: str = Field(min_length=1, description="Destination file or folder path")


class MoveFileTool(Tool[MoveFileArgs]):
    name: ClassVar[str] = "move_file"
    description: ClassVar[str] = "Move or rename a file or folder."
    Args: ClassVar[type[BaseModel]] = MoveFileArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "io"
    writes_files: ClassVar[bool] = True

    def preview(self, args: MoveFileArgs) -> str:
        return f"Move {args.src} -> {args.dst}"

    def run(self, args: MoveFileArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        src = Path(args.src)
        dst = Path(args.dst)
        if not src.exists():
            return ToolResult(ok=False, summary=f"Source does not exist: {args.src}")

        try:
            if dst.parent:
                dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return ToolResult(ok=True, summary=f"Moved {args.src} -> {args.dst}")
        except OSError as exc:
            return ToolResult(ok=False, summary=f"Failed to move {args.src} -> {args.dst}: {exc}")

    def verify(self, args: MoveFileArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        return Path(args.dst).exists() and not Path(args.src).exists()


# ---------------------------------------------------------------- write_file


class WriteFileArgs(BaseModel):
    path: str = Field(min_length=1, description="Path of the file to write")
    content: str = Field(description="New content to write to the file")


class WriteFileTool(Tool[WriteFileArgs]):
    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = "Write or overwrite content to a file inside allowed folders."
    Args: ClassVar[type[BaseModel]] = WriteFileArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "io"
    writes_files: ClassVar[bool] = True

    def preview(self, args: WriteFileArgs) -> str:
        p = Path(args.path)
        old_text = ""
        if p.exists() and p.is_file():
            try:
                old_text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                old_text = ""

        diff_lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                args.content.splitlines(keepends=True),
                fromfile=f"a/{args.path}",
                tofile=f"b/{args.path}",
            )
        )
        if not diff_lines:
            return f"write_file({args.path}) (no changes)"
        return "".join(diff_lines)

    def run(self, args: WriteFileArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        fpath = Path(args.path)
        try:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(args.content, encoding="utf-8", newline="")
            return ToolResult(
                ok=True,
                data={"path": str(fpath), "bytes": len(args.content.encode("utf-8"))},
                summary=f"Wrote {len(args.content)} characters to {args.path}",
            )
        except OSError as exc:
            return ToolResult(ok=False, summary=f"Failed to write {args.path}: {exc}")

    def verify(self, args: WriteFileArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        fpath = Path(args.path)
        if not fpath.exists():
            return False
        expected_hash = hashlib.sha256(args.content.encode("utf-8")).hexdigest()
        actual_hash = hashlib.sha256(fpath.read_bytes()).hexdigest()
        return expected_hash == actual_hash


# ---------------------------------------------------------------- trash_file


class TrashFileArgs(BaseModel):
    paths: list[str] = Field(
        min_length=1, description="List of file or directory paths to move to recycle bin"
    )


class TrashFileTool(Tool[TrashFileArgs]):
    name: ClassVar[str] = "trash_file"
    description: ClassVar[str] = "Safely move files or folders to the recycle bin."
    Args: ClassVar[type[BaseModel]] = TrashFileArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "io"
    writes_files: ClassVar[bool] = True

    def preview(self, args: TrashFileArgs) -> str:
        lines = [f"Move {len(args.paths)} item(s) to recycle bin:"]
        for p in args.paths:
            lines.append(f"- {p}")
        return "\n".join(lines)

    def run(self, args: TrashFileArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        trashed: list[str] = []
        errors: list[str] = []

        for raw in args.paths:
            fpath = Path(raw)
            if not fpath.exists():
                errors.append(f"Not found: {raw}")
                continue
            try:
                send2trash.send2trash(str(fpath))
                trashed.append(raw)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{raw}: {exc}")

        if errors and not trashed:
            return ToolResult(
                ok=False,
                summary=f"Failed to trash items: {'; '.join(errors)}",
            )

        summary = f"Moved {len(trashed)} item(s) to recycle bin"
        if errors:
            summary += f" ({len(errors)} error(s): {'; '.join(errors)})"
        return ToolResult(ok=True, data={"trashed": trashed, "errors": errors}, summary=summary)

    def verify(self, args: TrashFileArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        return all(not Path(p).exists() for p in args.paths)

