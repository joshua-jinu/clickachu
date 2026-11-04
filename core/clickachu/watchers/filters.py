"""Proactive watcher filters, signature normalisation, and debouncing (PLAN §16)."""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from clickachu.api.protocol import ContextSnapshot, SuggestionDecision, SuggestionEvent
from clickachu.context.capture import ContextStore
from clickachu.context.redact import redact
from clickachu.db.conn import Database
from clickachu.projects.profiles import ProjectStore
from clickachu.settings import SettingsStore
from clickachu.watchers.process_manager import ProcessManager

log = logging.getLogger(__name__)

# §16 regex: triggers on non-zero exit or log line matching:
ERROR_PATTERN = re.compile(
    r"(Traceback|Error:|ERR!|FATAL|Unhandled|ECONNREFUSED|EADDRINUSE|ENOENT|Exception)"
)

DEBOUNCE_SECONDS = 300.0  # 5 minutes
MAX_PENDING_SUGGESTIONS = 3
SNOOZE_SECONDS = 1800.0  # 30 minutes


def is_error_line(line: str) -> bool:
    """Return True if the log line matches the §16 error regex."""
    return bool(ERROR_PATTERN.search(line))


def normalise_signature(process_name: str, line: str) -> str:
    """Normalise process and error line into a debouncing signature.

    Strips timestamps, non-port numbers, and collapses file paths to basenames.
    """
    s = line

    # 1. Strip timestamps (ISO 8601, bracketed timestamps, date/time)
    s = re.sub(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
        "",
        s,
    )
    s = re.sub(r"\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]", "", s)
    s = re.sub(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b", "", s)
    s = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", s)

    # 2. Paths -> basename (Windows and POSIX)
    s = re.sub(r"[a-zA-Z]:\\(?:[^\s\\/:*?\"<>|\r\n]+\\)+([^\s\\/:*?\"<>|\r\n]+)", r"\1", s)
    s = re.sub(r"/(?:[^\s/]+/)+([^\s/]+)", r"\1", s)
    s = re.sub(r"(?:[\w.-]+\\)+([\w.-]+\.\w+)", r"\1", s)

    # 3. Preserve ports (e.g. :3000, :::3000, port 6379, etc.)
    ports: list[str] = []

    def _save_port(m: re.Match[str]) -> str:
        ports.append(m.group(0))
        return f"__PORT_{len(ports) - 1}__"

    s = re.sub(r"(?::+|\bport\s+)[0-9]{1,5}\b", _save_port, s, flags=re.IGNORECASE)

    # 4. Strip non-port numbers, hex addresses, line/col references
    s = re.sub(r"0x[0-9a-fA-F]+", "", s)
    s = re.sub(r"\b\d+\b", "", s)

    # 5. Restore port markers
    for i, port in enumerate(ports):
        s = s.replace(f"__PORT_{i}__", port)

    # 6. Normalize separators and whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Clean up empty colons/dots left behind by IP address stripping (e.g. "..." from "127.0.0.1")
    s = re.sub(r"\.{2,}", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    return f"{process_name}:{s}"


class SuggestionManager:
    """Tracks errors from managed processes, creates snapshots, debounces, and broadcasts."""

    def __init__(
        self,
        db: Database,
        context_store: ContextStore,
        project_store: ProjectStore,
        settings_store: SettingsStore,
        process_manager: ProcessManager,
        emit: Callable[[dict[str, Any]], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db = db
        self.context_store = context_store
        self.project_store = project_store
        self.settings_store = settings_store
        self.process_manager = process_manager
        self.emit = emit
        self._clock = clock
        self._last_suggested: dict[str, float] = {}
        self._active_snoozes: dict[str, asyncio.Task[None]] = {}
        self._installed = False

    def install(self) -> None:
        if not self._installed:
            self.process_manager.add_log_listener(self.on_log_line)
            self.process_manager.add_exit_listener(self.on_process_exit)
            self._installed = True

    def uninstall(self) -> None:
        if self._installed:
            self.process_manager.remove_log_listener(self.on_log_line)
            self.process_manager.remove_exit_listener(self.on_process_exit)
            self._installed = False
        for task in self._active_snoozes.values():
            task.cancel()
        self._active_snoozes.clear()

    def pending_count(self) -> int:
        conn = self.db.conn()
        row = conn.execute(
            "SELECT count(*) as cnt FROM suggestions WHERE status = 'pending'"
        ).fetchone()
        return int(row["cnt"]) if row else 0

    def can_suggest(self, signature: str) -> bool:
        settings = self.settings_store.get()
        if not getattr(settings, "proactive_suggestions", True):
            return False

        now = self._clock()
        last = self._last_suggested.get(signature)
        if last is not None and (now - last) < DEBOUNCE_SECONDS:
            return False

        if self.pending_count() >= MAX_PENDING_SUGGESTIONS:
            return False

        return True

    def create_suggestion(
        self,
        process_name: str,
        signature: str,
        title: str,
        body: str,
    ) -> SuggestionEvent | None:
        if not self.can_suggest(signature):
            return None

        now = self._clock()
        self._last_suggested[signature] = now

        # Get last 60 log lines from process manager and redact them
        raw_logs = self.process_manager.get_logs(process_name, lines=60)
        redacted_lines = [redact(line) for line in raw_logs]
        selection_text = "\n".join(redacted_lines)

        # Match project for context snapshot
        project_ref = self.project_store.match(
            window_title=f"{process_name} logs", process=process_name
        )

        context_id = f"ctx_{uuid.uuid4().hex}"
        snapshot = ContextSnapshot(
            id=context_id,
            captured_at=datetime.now(UTC),
            hwnd=0,
            process=process_name,
            app=process_name,
            window_title=f"{process_name} logs",
            project=project_ref,
            selection=selection_text or None,
            selection_method="none",
            selection_truncated=False,
            is_terminal=True,
        )
        self.context_store.put(snapshot)

        suggestion_id = f"sug_{uuid.uuid4().hex}"
        created_at = datetime.now(UTC).isoformat(timespec="seconds")

        conn = self.db.conn()
        with conn:
            conn.execute(
                "INSERT INTO suggestions (id, kind, signature, title, body, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (suggestion_id, "process_error", signature, title, body, created_at),
            )

        event = SuggestionEvent(
            id=suggestion_id,
            title=title,
            body=body,
            context_id=context_id,
        )
        try:
            self.emit(event.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to emit suggestion event: %s", exc)

        return event

    def on_log_line(self, process_name: str, line: str) -> None:
        if not is_error_line(line):
            return
        sig = normalise_signature(process_name, line)
        title = f"{process_name} error"
        body = f"{process_name}: {line.strip()}"
        self.create_suggestion(process_name, sig, title, body)

    def on_process_exit(self, process_name: str, exit_code: int) -> None:
        if exit_code == 0:
            return
        # If the process exited non-zero, trigger a crash suggestion
        logs = self.process_manager.get_logs(process_name, lines=10)
        last_error = ""
        for line in reversed(logs):
            if is_error_line(line):
                last_error = line
                break
        sig = normalise_signature(process_name, f"exit_{exit_code} {last_error}")
        title = f"{process_name} failed"
        body = (
            f"Process {process_name} crashed (exit {exit_code}): {last_error.strip()}"
            if last_error
            else f"Process {process_name} crashed with exit code {exit_code}"
        )
        self.create_suggestion(process_name, sig, title, body)

    def respond(self, suggestion_id: str, decision: SuggestionDecision) -> None:
        conn = self.db.conn()
        row = conn.execute(
            "SELECT id, kind, signature, title, body FROM suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
        if not row:
            log.warning("suggestion %s not found", suggestion_id)
            return

        if decision == "dismiss":
            with conn:
                conn.execute(
                    "UPDATE suggestions SET status = 'dismissed' WHERE id = ?",
                    (suggestion_id,),
                )
        elif decision == "accept":
            with conn:
                conn.execute(
                    "UPDATE suggestions SET status = 'accepted' WHERE id = ?",
                    (suggestion_id,),
                )
        elif decision == "later":
            with conn:
                conn.execute(
                    "UPDATE suggestions SET status = 'later' WHERE id = ?",
                    (suggestion_id,),
                )
            # Schedule re-surfacing after 30 minutes
            self._schedule_resurface(suggestion_id)

    def _schedule_resurface(self, suggestion_id: str) -> None:
        async def _resurface() -> None:
            await asyncio.sleep(SNOOZE_SECONDS)
            conn = self.db.conn()
            row = conn.execute(
                "SELECT id, kind, signature, title, body FROM suggestions "
                "WHERE id = ? AND status = 'later'",
                (suggestion_id,),
            ).fetchone()
            if not row:
                return
            with conn:
                conn.execute(
                    "UPDATE suggestions SET status = 'pending' WHERE id = ?",
                    (suggestion_id,),
                )
            event = SuggestionEvent(
                id=row["id"],
                title=row["title"],
                body=row["body"],
            )
            try:
                self.emit(event.model_dump())
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to re-surface suggestion: %s", exc)

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_resurface())
            self._active_snoozes[suggestion_id] = task
        except RuntimeError:
            pass
