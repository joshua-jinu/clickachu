"""Managed process lifecycle, Windows Job Objects, and log capture (PLAN §15)."""

import atexit
import logging
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

MAX_LOG_LINES = 1000
DEFAULT_PORT_TIMEOUT_S = 60.0

LogListener = Callable[[str, str], None]  # (name, line)
ExitListener = Callable[[str, int], None]  # (name, exit_code)


def _create_kill_on_close_job() -> Any | None:
    """Create a Windows Job Object configured with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE."""
    if sys.platform != "win32":
        return None
    try:
        import win32job

        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
        return job
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to create Job Object: %s", exc)
        return None


def _assign_process_to_job(job: Any, proc: subprocess.Popen[Any]) -> None:
    if sys.platform != "win32" or job is None:
        return
    try:
        import win32job

        # proc._handle is the Win32 process HANDLE
        handle = getattr(proc, "_handle", None)
        if handle is not None:
            win32job.AssignProcessToJobObject(job, int(handle))
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to assign process %s to Job Object: %s", proc.pid, exc)


@dataclass
class ManagedProcess:
    name: str
    cmd: str
    cwd: str
    port: int | None
    pid: int
    started_at: str
    process: subprocess.Popen[str]
    job_handle: Any = None
    exit_code: int | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    _reader_thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        if self.exit_code is not None:
            return False
        return self.process.poll() is None


class ProcessManager:
    def __init__(self) -> None:
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.Lock()
        self._log_listeners: list[LogListener] = []
        self._exit_listeners: list[ExitListener] = []

    def add_log_listener(self, cb: LogListener) -> None:
        with self._lock:
            if cb not in self._log_listeners:
                self._log_listeners.append(cb)

    def remove_log_listener(self, cb: LogListener) -> None:
        with self._lock:
            if cb in self._log_listeners:
                self._log_listeners.remove(cb)

    def add_exit_listener(self, cb: ExitListener) -> None:
        with self._lock:
            if cb not in self._exit_listeners:
                self._exit_listeners.append(cb)

    def remove_exit_listener(self, cb: ExitListener) -> None:
        with self._lock:
            if cb in self._exit_listeners:
                self._exit_listeners.remove(cb)

    def start(
        self, name: str, cmd: str, cwd: str, port: int | None = None
    ) -> ManagedProcess:
        """Start a managed process inside a Windows Job Object with log streaming."""
        with self._lock:
            existing = self._processes.get(name)
            if existing and existing.is_running:
                self._stop_locked(existing, timeout=3.0)

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            # Security note: shell=True is required on Windows to resolve npm/bat scripts and
            # shell commands defined in user-approved project profiles. Commands only run via
            # project_start (Tier 2 policy approval).
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                shell=True,
                creationflags=creationflags,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )

            job = _create_kill_on_close_job()
            _assign_process_to_job(job, proc)

            managed = ManagedProcess(
                name=name,
                cmd=cmd,
                cwd=cwd,
                port=port,
                pid=proc.pid,
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                process=proc,
                job_handle=job,
            )
            self._processes[name] = managed

            reader = threading.Thread(
                target=self._read_output,
                args=(managed,),
                name=f"proc-reader-{name}",
                daemon=True,
            )
            managed._reader_thread = reader
            reader.start()

            log.info("started managed process %r (pid=%s): %s", name, proc.pid, cmd)
            return managed

    def stop(self, name: str, timeout: float = 5.0) -> bool:
        """Stop a managed process."""
        with self._lock:
            managed = self._processes.get(name)
            if not managed:
                return True
            return self._stop_locked(managed, timeout=timeout)

    def _stop_locked(self, managed: ManagedProcess, timeout: float = 5.0) -> bool:
        if not managed.is_running:
            return True

        proc = managed.process
        try:
            if sys.platform == "win32":
                # Tree-kill to cleanly terminate shell + children
                subprocess.run(
                    ["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                proc.terminate()
        except OSError as exc:
            log.warning("error killing process %s (%s): %s", managed.name, proc.pid, exc)

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass

        managed.exit_code = proc.returncode
        return not managed.is_running

    def stop_all(self, timeout: float = 5.0) -> None:
        """Stop all managed processes."""
        with self._lock:
            for managed in list(self._processes.values()):
                if managed.is_running:
                    self._stop_locked(managed, timeout=timeout)

    def get_logs(self, name: str, lines: int = 200) -> list[str]:
        with self._lock:
            managed = self._processes.get(name)
            if not managed:
                return []
            return list(managed.logs)[-lines:]

    def is_running(self, name: str) -> bool:
        with self._lock:
            managed = self._processes.get(name)
            return bool(managed and managed.is_running)

    def get_process(self, name: str) -> ManagedProcess | None:
        with self._lock:
            return self._processes.get(name)

    def list_processes(self) -> list[ManagedProcess]:
        with self._lock:
            return list(self._processes.values())

    def wait_for_port(self, port: int, timeout: float = DEFAULT_PORT_TIMEOUT_S) -> bool:
        """Poll 127.0.0.1:port readiness until listening or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return True
            except (OSError, ConnectionRefusedError):
                time.sleep(0.2)
        return False

    def _read_output(self, managed: ManagedProcess) -> None:
        proc = managed.process
        if proc.stdout is None:
            return
        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                with self._lock:
                    managed.logs.append(line)
                    listeners = list(self._log_listeners)
                for cb in listeners:
                    try:
                        cb(managed.name, line)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("log listener exception: %s", exc)
        except (ValueError, OSError) as exc:
            log.debug("reading output for %s ended: %s", managed.name, exc)
        finally:
            proc.wait()
            exit_code = proc.returncode if proc.returncode is not None else -1
            with self._lock:
                managed.exit_code = exit_code
                listeners = list(self._exit_listeners)
            for cb in listeners:
                try:
                    cb(managed.name, exit_code)
                except Exception as exc:  # noqa: BLE001
                    log.debug("exit listener exception: %s", exc)


_process_manager = ProcessManager()


def get_process_manager() -> ProcessManager:
    return _process_manager


def shutdown_process_manager() -> None:
    _process_manager.stop_all()


atexit.register(shutdown_process_manager)
