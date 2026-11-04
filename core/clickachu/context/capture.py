"""Context snapshots (PLAN §7, §10): friendly names, truncation, redaction and the LRU store."""

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from typing import Any, Protocol

from clickachu.api.protocol import ContextSnapshot, ProjectRef, ReplaceMode, ReplaceResponse
from clickachu.context.redact import redact
from clickachu.context.selection import (
    ClipboardRestore,
    PasteOutcome,
    RawSelection,
    Win32,
    capture_selection,
    paste_back,
    restore_clipboard,
)

log = logging.getLogger(__name__)

MAX_SELECTION_CHARS = 20_000
# Bounds the regex work in redact() for pathological clipboards.
MAX_REDACT_INPUT_CHARS = 200_000
LRU_MAX_ENTRIES = 50
LRU_TTL_S = 30 * 60
CLIPBOARD_RESTORE_DELAY_S = 0.3
CAPTURE_TIMEOUT_S = 3.0
REPLACE_TIMEOUT_S = 3.0

# Runs a function on the COM worker (`automation.com_worker.run_com`); injectable for tests.
ComRunner = Callable[..., Awaitable[Any]]

FRIENDLY_APP_NAMES = {
    "code.exe": "VS Code",
    "cursor.exe": "Cursor",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "notepad.exe": "Notepad",
    "notepad++.exe": "Notepad++",
    "windowsterminal.exe": "Windows Terminal",
    "powershell.exe": "Windows PowerShell",
    "pwsh.exe": "PowerShell",
    "cmd.exe": "Command Prompt",
    "wsl.exe": "WSL",
    "explorer.exe": "File Explorer",
    "winword.exe": "Word",
    "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint",
    "outlook.exe": "Outlook",
    "onenote.exe": "OneNote",
    "devenv.exe": "Visual Studio",
    "slack.exe": "Slack",
    "discord.exe": "Discord",
    "ms-teams.exe": "Teams",
    "spotify.exe": "Spotify",
}


def friendly_app_name(process: str) -> str:
    if not process:
        return "Unknown app"
    known = FRIENDLY_APP_NAMES.get(process.lower())
    if known:
        return known
    stem = PureWindowsPath(process).stem
    return stem.replace("_", " ").replace("-", " ").title() or process


class ProjectMatcher(Protocol):
    def match(self, window_title: str, process: str) -> ProjectRef | None: ...


class NoProjects:
    """No project profiles: nothing ever matches."""

    def match(self, window_title: str, process: str) -> ProjectRef | None:
        return None


class ContextStore:
    """In-memory LRU of snapshots: 50 entries, 30 min TTL (§10 step 8)."""

    def __init__(
        self,
        max_entries: int = LRU_MAX_ENTRIES,
        ttl_s: float = LRU_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_entries
        self._ttl = ttl_s
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, ContextSnapshot]] = OrderedDict()

    def put(self, snapshot: ContextSnapshot) -> None:
        self._items[snapshot.id] = (self._clock(), snapshot)
        self._items.move_to_end(snapshot.id)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def get(self, context_id: str) -> ContextSnapshot | None:
        entry = self._items.get(context_id)
        if entry is None:
            return None
        stored_at, snapshot = entry
        if self._clock() - stored_at > self._ttl:
            del self._items[context_id]
            return None
        self._items.move_to_end(context_id)
        return snapshot

    def __len__(self) -> int:
        return len(self._items)


def build_snapshot(raw: RawSelection, projects: ProjectMatcher | None = None) -> ContextSnapshot:
    selection: str | None = None
    truncated = False
    if raw.text:
        # Redact before truncating so a secret straddling the cut can't leave a readable prefix.
        cleaned = redact(raw.text[:MAX_REDACT_INPUT_CHARS])
        truncated = len(raw.text) > MAX_SELECTION_CHARS
        selection = cleaned[:MAX_SELECTION_CHARS] or None
        truncated = truncated or len(cleaned) > MAX_SELECTION_CHARS
    return ContextSnapshot(
        id=f"ctx_{uuid.uuid4().hex}",
        captured_at=datetime.now(UTC),
        hwnd=raw.hwnd,
        process=raw.process,
        app=friendly_app_name(raw.process),
        window_title=raw.title,
        project=(projects or NoProjects()).match(raw.title, raw.process),
        selection=selection,
        selection_method=raw.method if selection else "none",
        selection_truncated=truncated,
        is_terminal=raw.is_terminal,
    )


class ContextService:
    def __init__(
        self,
        *,
        api: Win32,
        run_com: ComRunner,
        store: ContextStore | None = None,
        projects: ProjectMatcher | None = None,
        restore_delay_s: float = CLIPBOARD_RESTORE_DELAY_S,
    ) -> None:
        self._api = api
        self._run_com = run_com
        self.store = store if store is not None else ContextStore()
        self._projects = projects if projects is not None else NoProjects()
        self._restore_delay_s = restore_delay_s
        self._restores: set[asyncio.Task[None]] = set()

    async def capture(self) -> ContextSnapshot:
        raw: RawSelection = await self._run_com(
            capture_selection, self._api, timeout=CAPTURE_TIMEOUT_S
        )
        snapshot = build_snapshot(raw, self._projects)
        self.store.put(snapshot)
        log.info(
            "captured context %s: app=%s method=%s chars=%d terminal=%s",
            snapshot.id,
            snapshot.app,
            snapshot.selection_method,
            len(snapshot.selection or ""),
            snapshot.is_terminal,
        )
        return snapshot

    async def replace(self, context_id: str, text: str, mode: ReplaceMode) -> ReplaceResponse:
        snapshot = self.store.get(context_id)
        if snapshot is None or not snapshot.hwnd:
            return ReplaceResponse(ok=False, reason="expired")
        try:
            outcome: PasteOutcome = await self._run_com(
                paste_back,
                self._api,
                snapshot.hwnd,
                text,
                mode,
                snapshot.is_terminal,
                timeout=REPLACE_TIMEOUT_S,
            )
        except OSError as exc:
            log.warning("replace failed on the clipboard: %s", exc)
            return ReplaceResponse(ok=False, reason="focus")
        if outcome.restore is not None:
            # The target app reads the clipboard asynchronously; restore only after it had time.
            task = asyncio.create_task(self._restore_later(outcome.restore))
            self._restores.add(task)
            task.add_done_callback(self._restores.discard)
        if not outcome.ok:
            log.info("replace could not focus window %d; text left on clipboard", snapshot.hwnd)
        return ReplaceResponse(ok=outcome.ok, reason=outcome.reason)

    async def _restore_later(self, restore: ClipboardRestore) -> None:
        await asyncio.sleep(self._restore_delay_s)
        try:
            await self._run_com(restore_clipboard, self._api, restore, timeout=REPLACE_TIMEOUT_S)
        except (TimeoutError, OSError, RuntimeError) as exc:
            log.warning("clipboard restore failed: %s", exc)
