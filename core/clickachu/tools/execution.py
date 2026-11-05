"""Hybrid execution: isolated Docker Python, or individually approved host scripts.

Host scripts have the user's OS permissions. Path checks validate the working directory,
not every operation in arbitrary code. Never present host execution as a sandbox.
"""

import asyncio
import codecs
import hashlib
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import ClassVar, Literal

import psutil
from pydantic import BaseModel, Field, model_validator

from clickachu.api.protocol import StepOutputEvent
from clickachu.context.redact import redact
from clickachu.projects.roots import RootStore
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult
from clickachu.tools.policy import check_path_allowed, resolve_safe_path

OUTPUT_LIMIT = 16_000


def task_workspace(ctx: ToolContext) -> Path:
    task_id = hashlib.sha256((ctx.conversation_id or ctx.run_id).encode()).hexdigest()[:24]
    workspace = ctx.db.path.parent / "workspaces" / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace.resolve()


class ExecuteCodeArgs(BaseModel):
    code: str = Field(min_length=1, max_length=40_000)
    purpose: str = Field(min_length=1, max_length=400)
    language: Literal["python", "powershell"] = "python"
    environment: Literal["isolated", "host"] = "isolated"
    cwd: str | None = Field(
        default=None, description="Required allowed writable directory for host"
    )
    timeout_seconds: int = Field(default=30, ge=1, le=120)

    @model_validator(mode="after")
    def valid_environment(self):
        if self.environment == "isolated" and self.language != "python":
            raise ValueError("isolated execution supports Python only")
        if self.environment == "isolated" and self.cwd:
            raise ValueError("isolated code uses its task workspace, not host directories")
        if self.environment == "host" and not self.cwd:
            raise ValueError("host execution requires an explicit working directory")
        return self


def kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for process in reversed(children):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
    except psutil.NoSuchProcess:
        pass


def attach_job(pid: int):
    """Keep descendants attached to the execution lifetime on Windows."""
    if sys.platform != "win32":
        return None
    import win32api
    import win32job

    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    handle = win32api.OpenProcess(0x0001 | 0x0100, False, pid)
    try:
        win32job.AssignProcessToJobObject(job, handle)
    except Exception:
        job.Close()
        raise
    finally:
        handle.Close()
    return job


async def read_bounded(stream: asyncio.StreamReader, on_output=None) -> str:
    parts: list[bytes] = []
    retained = 0
    total = 0
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    while chunk := await stream.read(8192):
        total += len(chunk)
        if retained < OUTPUT_LIMIT:
            piece = chunk[: OUTPUT_LIMIT - retained]
            parts.append(piece)
            retained += len(piece)
            if on_output:
                pending += decoder.decode(piece)
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    await on_output(redact(line + "\n"))
    if on_output:
        pending += decoder.decode(b"", final=True)
        if pending:
            await on_output(redact(pending))
        if total > retained:
            await on_output("\n[output truncated]\n")
    text = b"".join(parts).decode("utf-8", errors="replace")
    return redact(text + ("\n[output truncated]" if total > retained else ""))


class ExecuteCodeTool(Tool[ExecuteCodeArgs]):
    name: ClassVar[str] = "execute_code"
    description: ClassVar[str] = (
        "Execute Python for custom/batch tasks in an isolated, network-disabled Docker workspace, "
        "or run Python/PowerShell on the PC after individual approval. Host scripts have full "
        "user-level access, not folder isolation. Prefer existing shortcuts for simple actions. "
        "Variables do not survive calls; files in the conversation workspace do. Print compact "
        "results and verify resulting files/actions. Never retry a partial change blindly. "
        "Child processes stop when the script ends; use open_app or managed project commands "
        "for persistent apps/services."
    )
    Args: ClassVar[type[BaseModel]] = ExecuteCodeArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    timeout_s: ClassVar[float] = 140

    def preview(self, args: ExecuteCodeArgs) -> str:
        scope = (
            "HOST ACCESS: runs with your Windows account's permissions; folder rules do not "
            "restrict what this script can access. Approval applies to this script only."
            if args.environment == "host"
            else "Isolated Python: task workspace only, no network, no host credentials."
        )
        return (
            f"{args.purpose}\n{scope}\nWorking directory: {args.cwd or 'task workspace'}\n"
            f"Timeout: {args.timeout_seconds}s\n\n{args.code}"
        )

    def read_paths(self, args):
        return ()

    def write_paths(self, args):
        return (args.cwd,) if args.cwd else ()

    async def run(self, args: ExecuteCodeArgs, ctx: ToolContext) -> ToolResult:
        settings = ctx.settings
        if settings is None or not settings.code_execution_enabled:
            return ToolResult(ok=False, summary="Code execution is disabled in Settings.")
        workspace = task_workspace(ctx)
        execution_id = uuid.uuid4().hex
        scripts = ctx.db.path.parent / "scripts" / workspace.name
        scripts.mkdir(parents=True, exist_ok=True)
        script = scripts / (execution_id + (".py" if args.language == "python" else ".ps1"))
        # -File/-u avoids Windows command-line length limits and preserves literal quoting.
        script.write_text(
            args.code, encoding="utf-8-sig" if args.language == "powershell" else "utf-8"
        )
        container = f"clickachu-{execution_id}"
        docker = None
        if args.environment == "host":
            if not settings.host_execution_enabled:
                return ToolResult(ok=False, summary="Host scripts are disabled in Settings.")
            roots = RootStore(ctx.db).list()
            if error := check_path_allowed(args.cwd, roots, require_write=True):
                return ToolResult(ok=False, summary=error)
            cwd = resolve_safe_path(args.cwd, roots)
            if cwd is None:
                return ToolResult(ok=False, summary="Working directory is invalid.")
            if not cwd.is_dir():
                return ToolResult(ok=False, summary="Working directory does not exist.")
            if args.language == "python":
                command = [sys.executable, "-I", "-u", str(script)]
            else:
                shell = shutil.which("pwsh") or shutil.which("powershell")
                if not shell:
                    return ToolResult(ok=False, summary="PowerShell is not installed.")
                script.write_text(
                    "$ErrorActionPreference='Stop';\n" + args.code, encoding="utf-8-sig"
                )
                command = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)]
        else:
            docker = shutil.which("docker")
            if not docker:
                return ToolResult(
                    ok=False,
                    summary="Isolated Python requires Docker Desktop and the "
                    "python:3.12-slim image. No script ran; no host fallback was attempted.",
                )
            cwd = workspace
            command = [
                docker,
                "run",
                "--rm",
                "--pull=never",
                "--name",
                container,
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=64",
                "--memory=512m",
                "--cpus=1",
                "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
                "--mount",
                f"type=bind,source={workspace},target=/workspace",
                "--mount",
                f"type=bind,source={script.resolve()},target=/run/task.py,readonly",
                "--workdir=/workspace",
                "python:3.12-slim",
                "python",
                "-I",
                "-u",
                "/run/task.py",
            ]
        # Do not inherit API keys or arbitrary application secrets into the child process.
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "PATH",
                "PATHEXT",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "PROGRAMFILES",
                "PROGRAMFILES(X86)",
                "COMMONPROGRAMFILES",
                "LOCALAPPDATA",
                "APPDATA",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_CONFIG",
            }
        }
        env["PYTHONIOENCODING"] = "utf-8"
        if args.environment == "host":
            from clickachu.tools.computer import invalidate_observations

            invalidate_observations(ctx.run_id)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
        )
        try:
            job = attach_job(process.pid)
        except Exception:
            kill_tree(process.pid)
            await process.wait()
            raise

        async def publish(stream, text):
            if ctx.emit:
                await ctx.emit(
                    StepOutputEvent(
                        run_id=ctx.run_id, step_id=ctx.step_id, stream=stream, text=text
                    )
                )

        stdout_task = asyncio.create_task(
            read_bounded(process.stdout, lambda text: publish("stdout", text))
        )
        stderr_task = asyncio.create_task(
            read_bounded(process.stderr, lambda text: publish("stderr", text))
        )
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=args.timeout_seconds)
        except TimeoutError:
            timed_out = True
            kill_tree(process.pid)
        finally:
            if process.returncode is None:
                kill_tree(process.pid)
            if job is not None:
                job.Close()
            # Docker's client can exit while the container is still running.
            if docker:
                cleanup = await asyncio.create_subprocess_exec(
                    docker,
                    "rm",
                    "-f",
                    container,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
                )
                try:
                    await asyncio.wait_for(cleanup.wait(), timeout=5)
                except TimeoutError:
                    kill_tree(cleanup.pid)
            await process.wait()
            # A detached child may retain pipe handles; do not wait indefinitely.
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task),
                    timeout=3,
                )
            except TimeoutError:
                stdout, stderr = "", "Output pipes remained open after execution."
        ok = not timed_out and process.returncode == 0
        summary = (
            f"Script timed out after {args.timeout_seconds}s; partial changes may exist."
            if timed_out
            else f"{args.language} exited with code {process.returncode}."
        )
        return ToolResult(
            ok=ok,
            summary=summary,
            data={
                "execution_id": execution_id,
                "environment": args.environment,
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "workspace": str(workspace),
            },
        )
