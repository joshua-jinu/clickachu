"""Process and project command tools (PLAN §13.3, §15)."""

import logging
import re
from typing import ClassVar

from pydantic import BaseModel, Field

from clickachu.projects.profiles import ProjectStore
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult, WorkerName
from clickachu.watchers.process_manager import get_process_manager

log = logging.getLogger(__name__)


def process_list_requested(text: str) -> bool:
    """Live PC inspection, not an explanation of what processes are."""
    if re.match(r"\s*(?:how|why|explain|what\s+is|what\s+are)\b", text, re.I):
        return False
    return bool(re.search(r"\b(?:processes|running\s+apps|task\s+list)\b", text, re.I)) and bool(
        re.search(r"\b(?:check|list|show|display|running|scan)\b", text, re.I)
    )


class ListProcessesArgs(BaseModel):
    name: str = Field(default="", description="Optional case-insensitive process-name filter")


class ListProcessesTool(Tool[ListProcessesArgs]):
    name: ClassVar[str] = "list_processes"
    description: ClassVar[str] = (
        "Read all running PC processes directly. Returns real PIDs, memory MiB and cumulative "
        "CPU seconds, not CPU percent. Does not need a terminal or administrator rights."
    )
    Args: ClassVar[type[BaseModel]] = ListProcessesArgs
    tier: ClassVar[Tier] = Tier.READ

    def run(self, args: ListProcessesArgs, ctx: ToolContext) -> ToolResult:
        import psutil

        rows = []
        for process in psutil.process_iter(
            ["pid", "name", "memory_info", "cpu_times"], ad_value=None
        ):
            try:
                info = process.info
                name = info["name"] or "(restricted)"
                if args.name and args.name.casefold() not in name.casefold():
                    continue
                memory, cpu = info["memory_info"], info["cpu_times"]
                rows.append(
                    {
                        "name": name,
                        "pid": info["pid"],
                        "memory_mib": round(memory.rss / 1048576, 1) if memory else None,
                        "cpu_seconds": round(cpu.user + cpu.system, 1) if cpu else None,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        rows.sort(key=lambda row: row["memory_mib"] or 0, reverse=True)
        lines = [
            f"{len(rows)} running processes, sorted by memory.",
            "",
            "| Process | PID | Memory (MiB) | CPU time (s) |",
            "|---|---:|---:|---:|",
        ]
        for row in rows:
            name = row["name"].replace("|", "\\|").replace("\n", " ").replace("\r", " ")
            lines.append(
                f"| {name} | {row['pid']} | "
                f"{row['memory_mib'] if row['memory_mib'] is not None else '—'} | "
                f"{row['cpu_seconds'] if row['cpu_seconds'] is not None else '—'} |"
            )
        return ToolResult(
            ok=True,
            summary=f"Read {len(rows)} running processes.",
            data=rows,
            untrusted_text="\n".join(lines),
        )


# ---------------------------------------------------------------- get_process_logs


class GetProcessLogsArgs(BaseModel):
    name: str = Field(min_length=1, description="Managed process name or project:command")
    lines: int = Field(
        default=200,
        ge=1,
        le=200,
        description="Maximum recent log lines to retrieve (1..200)",
    )


class GetProcessLogsTool(Tool[GetProcessLogsArgs]):
    name: ClassVar[str] = "get_process_logs"
    description: ClassVar[str] = (
        "Retrieve recent stdout and stderr lines for a process started by Clickachu."
    )
    Args: ClassVar[type[BaseModel]] = GetProcessLogsArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "io"

    def preview(self, args: GetProcessLogsArgs) -> str:
        return f"Get logs for process {args.name!r} (max {args.lines} lines)"

    def run(self, args: GetProcessLogsArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        pm = get_process_manager()
        logs = pm.get_logs(args.name, lines=args.lines)
        summary = f"Retrieved {len(logs)} log line(s) for {args.name!r}"
        return ToolResult(
            ok=True,
            data=logs,
            summary=summary,
            untrusted_text="\n".join(logs) if logs else "No logs recorded for this process.",
        )


# ---------------------------------------------------------------- stop_process


class StopProcessArgs(BaseModel):
    name: str = Field(min_length=1, description="Managed process name or project:command to stop")


class StopProcessTool(Tool[StopProcessArgs]):
    name: ClassVar[str] = "stop_process"
    description: ClassVar[str] = "Stop a process currently managed by Clickachu."
    Args: ClassVar[type[BaseModel]] = StopProcessArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "io"

    def preview(self, args: StopProcessArgs) -> str:
        return f"Stop process {args.name!r}"

    def run(self, args: StopProcessArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        pm = get_process_manager()
        stopped = pm.stop(args.name)
        if stopped:
            return ToolResult(ok=True, summary=f"Stopped process {args.name!r}")
        return ToolResult(ok=False, summary=f"Failed to stop process {args.name!r}")

    def verify(self, args: StopProcessArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        pm = get_process_manager()
        return not pm.is_running(args.name)


# ---------------------------------------------------------------- run_project_command


class RunProjectCommandArgs(BaseModel):
    project: str = Field(min_length=1, description="Registered project profile name")
    command: str = Field(min_length=1, description="Command name defined in the project profile")


class RunProjectCommandTool(Tool[RunProjectCommandArgs]):
    name: ClassVar[str] = "run_project_command"
    description: ClassVar[str] = (
        "Run an approved project command from a saved project profile (literal cmd from profile)."
    )
    Args: ClassVar[type[BaseModel]] = RunProjectCommandArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "io"
    timeout_s: ClassVar[float] = 65.0

    def preview(self, args: RunProjectCommandArgs) -> str:
        return f"Run {args.command!r} in project {args.project!r}"

    def trust_key(self, args: RunProjectCommandArgs) -> str | None:
        return f"{args.project}:{args.command}"

    def run(self, args: RunProjectCommandArgs, ctx: ToolContext) -> ToolResult:
        project_store = ProjectStore(ctx.db)
        project = project_store.get_by_name(args.project)
        if not project:
            return ToolResult(
                ok=False,
                summary=f"Project {args.project!r} not found in saved profiles",
            )

        cmd_entry = next((c for c in project.commands if c.name == args.command), None)
        if not cmd_entry:
            valid_cmds = ", ".join(repr(c.name) for c in project.commands)
            return ToolResult(
                ok=False,
                summary=(
                    f"Command {args.command!r} not found in project {args.project!r}. "
                    f"Available commands: {valid_cmds}"
                ),
            )

        proc_name = f"{args.project}:{args.command}"
        pm = get_process_manager()
        managed = pm.start(
            name=proc_name,
            cmd=cmd_entry.cmd,
            cwd=cmd_entry.cwd,
            port=cmd_entry.port,
        )

        # Register unverified cleanup: if run is cancelled before verification, stop it
        ctx.unverified_cleanup.append(lambda: pm.stop(proc_name))

        summary = f"Started {proc_name} (pid {managed.pid})" + (
            f" listening on port {cmd_entry.port}" if cmd_entry.port else ""
        )
        return ToolResult(
            ok=True,
            data={
                "project": args.project,
                "command": args.command,
                "pid": managed.pid,
                "port": cmd_entry.port,
            },
            summary=summary,
        )

    def verify(self, args: RunProjectCommandArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        proc_name = f"{args.project}:{args.command}"
        pm = get_process_manager()
        if not pm.is_running(proc_name):
            return False

        # If a port was configured, verify port readiness
        if isinstance(result.data, dict) and result.data.get("port"):
            port = int(result.data["port"])
            return pm.wait_for_port(port, timeout=60.0)

        return True
