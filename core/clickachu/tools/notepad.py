"""One approved operation: create an empty Notepad document, paste, and read back.

Runs on the COM worker. Never clears an existing document or silently saves a file.
"""

import time
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from clickachu.automation.winapi import RealWin32
from clickachu.context.selection import wait_for_modifier_release
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult
from clickachu.tools.desktop import OpenAppArgs, OpenAppTool, _is_admin_error


def _editor_value(editor: Any) -> str | None:
    try:
        return str(editor.iface_text.DocumentRange.GetText(-1))
    except Exception:  # noqa: BLE001 - UIA patterns vary by Notepad version
        try:
            value = editor.get_value()
            return str(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None


def _notepad_editor(hwnd: int) -> Any:
    from pywinauto import Application

    window = Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)
    if window.element_info.class_name.lower() not in ("notepad", "applicationframewindow"):
        raise RuntimeError("A Notepad dialog is active; nothing was typed.")
    candidates = [
        c
        for c in window.descendants()
        if c.element_info.control_type in ("Document", "Edit") and c.is_visible() and c.is_enabled()
    ]
    documents = [c for c in candidates if c.element_info.control_type == "Document"]
    candidates = documents or [
        c
        for c in candidates
        if c.element_info.class_name.lower() in ("edit", "richeditd2dpt", "richedit50w")
    ]
    if len(candidates) != 1:
        raise RuntimeError("Could not identify one Notepad document editor; nothing was typed.")
    return candidates[0]


def _foreground_notepad(api: RealWin32, expected_hwnd: int | None = None) -> int:
    hwnd = api.foreground_window()
    if not hwnd or api.process_name(api.window_pid(hwnd)).lower() != "notepad.exe":
        raise RuntimeError("Notepad lost focus; stopped before sending input.")
    if expected_hwnd is not None and hwnd != expected_hwnd:
        raise RuntimeError("The target Notepad window changed; stopped before sending input.")
    return hwnd


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\r\n")


class WriteNotepadArgs(BaseModel):
    text: str = Field(min_length=1, max_length=20000, description="Document text to put in Notepad")


class WriteNotepadTool(Tool[WriteNotepadArgs]):
    name: ClassVar[str] = "write_notepad"
    description: ClassVar[str] = (
        "Open Notepad, create a fresh empty document, write the supplied text, and verify it. "
        "Leaves the document open and unsaved. Existing document text is never cleared."
    )
    Args = WriteNotepadArgs
    tier = Tier.CONFIRM
    worker = "com"
    timeout_s = 30.0

    def preview(self, args: WriteNotepadArgs) -> str:
        return f"Create an empty Notepad document and write:\n\n{args.text}\n\nLeave open, unsaved."

    def run(self, args: WriteNotepadArgs, ctx: ToolContext) -> ToolResult:
        try:
            opened = OpenAppTool().run(OpenAppArgs(name="notepad"), ctx)
            if not opened.ok or not isinstance(opened.data, dict) or not opened.data.get("hwnd"):
                return ToolResult(ok=False, summary="Could not locate Notepad; nothing was typed.")
            api = RealWin32()
            api.focus_window(opened.data["hwnd"])
            # Foreground changes and packaged-Notepad hand-off can be asynchronous.
            focus_deadline = time.monotonic() + 1.0
            while api.foreground_window() != opened.data["hwnd"]:
                if time.monotonic() >= focus_deadline:
                    raise RuntimeError("Windows refused to focus Notepad; nothing was typed.")
                time.sleep(0.05)
            if not wait_for_modifier_release(api):
                return ToolResult(ok=False, summary="Release keyboard modifiers and try again.")
            _foreground_notepad(api, opened.data["hwnd"])
            api.send_chord(0x11, 0x4E)  # Ctrl+N: new document, never clear an existing one

            editor = None
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                time.sleep(0.1)
                hwnd = _foreground_notepad(api)
                try:
                    candidate = _notepad_editor(hwnd)
                    value = _editor_value(candidate)
                    if value is not None and not value.strip():
                        editor = candidate
                        break
                except RuntimeError:
                    continue
            if editor is None:
                return ToolResult(
                    ok=False,
                    summary=(
                        "Could not verify an empty Notepad editor; "
                        "existing text was left untouched."
                    ),
                )

            editor.set_focus()
            _foreground_notepad(api, hwnd)
            value = _editor_value(editor)
            if value is None or _normalise(value) != "":
                return ToolResult(ok=False, summary="Editor is not empty; nothing was typed.")
            saved = api.get_clipboard_text()
            api.set_clipboard_text(args.text)
            sequence = api.clipboard_sequence()
            try:
                if not wait_for_modifier_release(api):
                    raise RuntimeError("Keyboard modifiers are held; nothing was typed.")
                _foreground_notepad(api, hwnd)
                if not editor.has_keyboard_focus():
                    raise RuntimeError("The document editor lost focus; nothing was typed.")
                api.send_chord(0x11, 0x56)
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    value = _editor_value(editor)
                    if value is not None and _normalise(value) == _normalise(args.text):
                        return ToolResult(
                            ok=True,
                            data={"hwnd": hwnd, "text_verified": True},
                            summary=(
                                f"Wrote {len(args.text)} characters in a new, "
                                "unsaved Notepad document."
                            ),
                        )
                    time.sleep(0.1)
                return ToolResult(
                    ok=False,
                    summary=(
                        "Pasted into the new Notepad document, but could not verify its contents. "
                        "Inspect it before retrying. No file was saved."
                    ),
                )
            finally:
                time.sleep(0.3)
                if api.clipboard_sequence() == sequence:
                    api.set_clipboard_text(saved)
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed, logged tool result
            message = "can't control admin windows" if _is_admin_error(exc) else str(exc)
            return ToolResult(ok=False, summary=f"Notepad operation stopped: {message}")

    def verify(self, args: WriteNotepadArgs, result: ToolResult) -> bool:
        if not result.ok or not isinstance(result.data, dict):
            return False
        try:
            value = _editor_value(_notepad_editor(result.data["hwnd"]))
            return value is not None and _normalise(value) == _normalise(args.text)
        except Exception:  # noqa: BLE001
            return False
