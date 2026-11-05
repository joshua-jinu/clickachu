"""Foreground-window selection capture and paste-back (PLAN §10).

All functions here must run on the COM worker thread (hard rule 6). They talk to Windows only
through the `Win32` protocol so tests can drive them with a fake.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from clickachu.api.protocol import ReplaceFailure, ReplaceMode, SelectionMethod

log = logging.getLogger(__name__)

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_C = 0x43
VK_V = 0x56
VK_RIGHT = 0x27

COPY = (VK_CONTROL, VK_C)
PASTE = (VK_CONTROL, VK_V)
# Plain Ctrl+C in a terminal is SIGINT, so terminals only ever get the Shift variants (§10).
TERMINAL_COPY = (VK_CONTROL, VK_SHIFT, VK_C)
TERMINAL_PASTE = (VK_CONTROL, VK_SHIFT, VK_V)

TERMINAL_PROCESSES = frozenset(
    {
        "windowsterminal.exe",
        "conhost.exe",
        "openconsole.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "wsl.exe",
        "mintty.exe",
    }
)
# Editors whose integrated terminal is an xterm.js element inside the editor window.
XTERM_HOSTS = frozenset({"code.exe", "cursor.exe"})

MODIFIER_RELEASE_TIMEOUT_S = 0.3
CAPTURE_MODIFIER_TIMEOUT_S = 1.2
CLIPBOARD_POLL_S = 0.02
CLIPBOARD_TIMEOUT_S = 0.75
FOCUS_TIMEOUT_S = 0.3


@dataclass(frozen=True)
class FocusedElement:
    class_name: str = ""
    # None: the element has no TextPattern (or UIA failed). "": pattern present, nothing selected.
    selection: str | None = None
    is_password: bool = False


class Win32(Protocol):
    def foreground_window(self) -> int: ...
    def is_window(self, hwnd: int) -> bool: ...
    def window_pid(self, hwnd: int) -> int: ...
    def process_name(self, pid: int) -> str: ...
    def window_title(self, hwnd: int) -> str: ...
    def is_own_process(self, pid: int) -> bool: ...
    def modifiers_down(self) -> bool: ...
    def focused_element(self) -> FocusedElement: ...
    def clipboard_sequence(self) -> int: ...
    def get_clipboard_text(self) -> str | None: ...
    def set_clipboard_text(self, text: str | None) -> None: ...
    def send_chord(self, *vks: int) -> None: ...
    def focus_window(self, hwnd: int) -> None: ...
    def sleep(self, seconds: float) -> None: ...
    def now(self) -> float: ...


@dataclass(frozen=True)
class RawSelection:
    hwnd: int
    process: str
    title: str
    text: str | None
    method: SelectionMethod
    is_terminal: bool


@dataclass(frozen=True)
class ClipboardRestore:
    text: str | None
    # Sequence number right after we set the clipboard; if it moved, someone else wrote since.
    expected_seq: int


@dataclass(frozen=True)
class PasteOutcome:
    ok: bool
    reason: ReplaceFailure | None = None
    restore: ClipboardRestore | None = None


def is_terminal(process: str, focused_class: str) -> bool:
    name = process.lower()
    if name in TERMINAL_PROCESSES:
        return True
    return name in XTERM_HOSTS and "xterm" in focused_class.lower()


def wait_for_modifier_release(api: Win32, timeout_s: float = MODIFIER_RELEASE_TIMEOUT_S) -> bool:
    """§10 step 2: a synthetic Ctrl+C must not arrive as Ctrl+Alt+C. False if still held."""
    deadline = api.now() + timeout_s
    while api.modifiers_down():
        if api.now() >= deadline:
            return False
        api.sleep(0.01)
    return True


def capture_selection(api: Win32) -> RawSelection:
    hwnd = api.foreground_window()
    if not hwnd:
        return RawSelection(0, "", "", None, "none", False)
    pid = api.window_pid(hwnd)
    process = api.process_name(pid)
    title = api.window_title(hwnd)

    def result(text: str | None, method: SelectionMethod, terminal: bool = False) -> RawSelection:
        found = text or None
        return RawSelection(hwnd, process, title, found, method if found else "none", terminal)

    # Never inject keystrokes into our own windows (the bar/panel can be foreground).
    if api.is_own_process(pid):
        return result(None, "none")

    released = wait_for_modifier_release(api, CAPTURE_MODIFIER_TIMEOUT_S)
    if api.foreground_window() != hwnd:
        return result(None, "none")
    focus = api.focused_element()
    terminal = is_terminal(process, focus.class_name)
    if api.foreground_window() != hwnd:
        return result(None, "none", terminal)
    if focus.is_password:
        return result(None, "none", terminal)
    if focus.selection:
        return result(focus.selection, "uia", terminal)
    if not released:
        log.info("modifiers still held; skipping clipboard fallback")
        return result(None, "none", terminal)
    # The user may have switched windows while we waited; keys must go where the selection is.
    if api.foreground_window() != hwnd:
        return result(None, "none", terminal)
    try:
        return result(_copy_via_clipboard(api, terminal), "clipboard", terminal)
    except OSError as exc:
        log.warning("clipboard capture failed: %s", exc)
        return result(None, "none", terminal)


def _copy_via_clipboard(api: Win32, terminal: bool) -> str | None:
    saved = api.get_clipboard_text()
    seq = api.clipboard_sequence()
    api.send_chord(*(TERMINAL_COPY if terminal else COPY))

    deadline = api.now() + CLIPBOARD_TIMEOUT_S
    while api.clipboard_sequence() == seq:
        if api.now() >= deadline:
            return None  # clipboard untouched: nothing was selected
        api.sleep(CLIPBOARD_POLL_S)
    try:
        return api.get_clipboard_text()
    finally:
        api.set_clipboard_text(saved)


def paste_back(api: Win32, hwnd: int, text: str, mode: ReplaceMode, terminal: bool) -> PasteOutcome:
    """§10 replace/insert. On a focus failure the text is left on the clipboard for Ctrl+V."""
    saved = api.get_clipboard_text()
    api.set_clipboard_text(text)
    seq = api.clipboard_sequence()

    if not api.is_window(hwnd) or not _focus(api, hwnd) or not wait_for_modifier_release(api):
        return PasteOutcome(ok=False, reason="focus")
    if mode == "insert" and not terminal:
        api.send_chord(VK_RIGHT)  # collapse the selection so the paste doesn't replace it
    api.send_chord(*(TERMINAL_PASTE if terminal else PASTE))
    return PasteOutcome(ok=True, restore=ClipboardRestore(saved, seq))


def restore_clipboard(api: Win32, restore: ClipboardRestore) -> None:
    if api.clipboard_sequence() != restore.expected_seq:
        log.info("clipboard changed since paste; not restoring")
        return
    api.set_clipboard_text(restore.text)


def _focus(api: Win32, hwnd: int) -> bool:
    if api.foreground_window() == hwnd:
        return True
    api.focus_window(hwnd)
    deadline = api.now() + FOCUS_TIMEOUT_S
    while api.foreground_window() != hwnd:
        if api.now() >= deadline:
            return False
        api.sleep(0.02)
    return True
