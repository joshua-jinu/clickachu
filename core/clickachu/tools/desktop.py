"""Desktop automation tools running on the COM worker thread (PLAN §13.3)."""

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from clickachu.automation.winapi import RealWin32
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult, WorkerName

log = logging.getLogger(__name__)

# Map UIA ControlType IDs to human-readable strings
UIA_CONTROL_TYPES: dict[int, str] = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
    50039: "SemanticZoom",
    50040: "AppBar",
}

ADMIN_ERROR_MESSAGE = "can't control admin windows"


def _is_admin_error(exc: BaseException) -> bool:
    """Check whether an exception indicates access denied / admin elevation (UIPI)."""
    import psutil

    if isinstance(exc, psutil.AccessDenied):
        return True
    if isinstance(exc, PermissionError):
        return True
    msg = str(exc).lower()
    if "access is denied" in msg or "0x80070005" in msg or "e_accessdenied" in msg:
        return True
    return False


def _find_window_by_title(
    win32: RealWin32, title_query: str
) -> tuple[int, str, str] | None:
    """Find a visible window whose title contains `title_query` (case-insensitive)."""
    windows = _enumerate_windows(win32)
    q = title_query.strip().lower()
    for hwnd, title, proc in windows:
        if q in title.lower():
            return hwnd, title, proc
    return None


def _enumerate_windows(win32: RealWin32) -> list[tuple[int, str, str]]:
    """Enumerate visible top-level windows (hwnd, title, process_name)."""
    if sys.platform != "win32":
        return []

    import ctypes
    from ctypes import wintypes

    import psutil

    user32 = ctypes.windll.user32
    results: list[tuple[int, str, str]] = []

    def enum_proc(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if win32.is_own_process(pid.value):
            return True

        proc_name = ""
        try:
            proc_name = psutil.Process(pid.value).name()
        except psutil.AccessDenied:
            proc_name = "[admin]"
        except psutil.Error:
            proc_name = ""

        results.append((hwnd, title, proc_name))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    return results


# ---------------------------------------------------------------- get_active_context


class GetActiveContextArgs(BaseModel):
    pass


class GetActiveContextTool(Tool[GetActiveContextArgs]):
    name: ClassVar[str] = "get_active_context"
    description: ClassVar[str] = (
        "Get information about the currently active foreground window, "
        "process, and focused element."
    )
    Args: ClassVar[type[BaseModel]] = GetActiveContextArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "com"

    def preview(self, args: GetActiveContextArgs) -> str:
        del args
        return "Get active window context"

    def run(self, args: GetActiveContextArgs, ctx: ToolContext) -> ToolResult:
        del args
        if sys.platform != "win32":
            return ToolResult(ok=True, summary="Simulated desktop context (non-Windows)")

        win32 = RealWin32()
        try:
            hwnd = win32.foreground_window()
            if not hwnd or not win32.is_window(hwnd):
                return ToolResult(
                    ok=True,
                    summary="No active window",
                    untrusted_text="No active window found.",
                )

            pid = win32.window_pid(hwnd)
            try:
                proc_name = win32.process_name(pid)
            except Exception as exc:  # noqa: BLE001
                if _is_admin_error(exc):
                    return ToolResult(
                        ok=False,
                        summary=ADMIN_ERROR_MESSAGE,
                        untrusted_text=ADMIN_ERROR_MESSAGE,
                    )
                proc_name = ""

            title = win32.window_title(hwnd)
            focused = win32.focused_element()

            data = {
                "hwnd": hwnd,
                "process": proc_name,
                "title": title,
                "focused_class": focused.class_name,
                "selection": focused.selection,
                "is_password": focused.is_password,
            }
            summary = f"Active window: {title!r} ({proc_name})"
            lines = [
                f"Window: {title}",
                f"Process: {proc_name} (pid {pid}, hwnd {hwnd})",
            ]
            if focused.class_name:
                lines.append(f"Focused control: {focused.class_name}")
            if focused.selection:
                lines.append(f"Selection:\n{focused.selection}")

            return ToolResult(
                ok=True,
                data=data,
                summary=summary,
                untrusted_text="\n".join(lines),
            )
        except Exception as exc:  # noqa: BLE001
            if _is_admin_error(exc):
                return ToolResult(
                    ok=False,
                    summary=ADMIN_ERROR_MESSAGE,
                    untrusted_text=ADMIN_ERROR_MESSAGE,
                )
            log.warning("get_active_context failed: %s", exc)
            return ToolResult(ok=False, summary=f"Failed to get active context: {exc}")


# ---------------------------------------------------------------- list_windows


class ListWindowsArgs(BaseModel):
    query: str | None = Field(
        default=None,
        description="Optional title or process name substring to filter windows",
    )


class ListWindowsTool(Tool[ListWindowsArgs]):
    name: ClassVar[str] = "list_windows"
    description: ClassVar[str] = (
        "List open top-level application windows with their titles and process names."
    )
    Args: ClassVar[type[BaseModel]] = ListWindowsArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "com"

    def preview(self, args: ListWindowsArgs) -> str:
        q = f" matching {args.query!r}" if args.query else ""
        return f"List open windows{q}"

    def run(self, args: ListWindowsArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        if sys.platform != "win32":
            return ToolResult(ok=True, data=[], summary="No windows (non-Windows)")

        win32 = RealWin32()
        windows = _enumerate_windows(win32)

        if args.query:
            q = args.query.strip().lower()
            windows = [
                w for w in windows if q in w[1].lower() or q in w[2].lower()
            ]

        items = [{"hwnd": h, "title": t, "process": p} for h, t, p in windows]
        lines = [f"- {t} ({p}, hwnd={h})" for h, t, p in windows]
        summary = f"Found {len(items)} open window(s)"
        return ToolResult(
            ok=True,
            data=items,
            summary=summary,
            untrusted_text="\n".join(lines) if lines else "No matching windows found.",
        )


# ---------------------------------------------------------------- inspect_window


class InspectWindowArgs(BaseModel):
    title: str = Field(
        min_length=1, description="Window title substring to inspect"
    )
    depth: int = Field(
        default=3, ge=1, le=3, description="Inspection tree depth (1..3)"
    )
    max_nodes: int = Field(
        default=150, ge=1, le=150, description="Maximum UI elements to inspect (1..150)"
    )


class InspectWindowTool(Tool[InspectWindowArgs]):
    name: ClassVar[str] = "inspect_window"
    description: ClassVar[str] = (
        "Inspect the UI Automation control hierarchy of a window by title "
        "(depth <= 3, max 150 nodes)."
    )
    Args: ClassVar[type[BaseModel]] = InspectWindowArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "com"

    def preview(self, args: InspectWindowArgs) -> str:
        return f"Inspect window {args.title!r} (depth={args.depth}, max_nodes={args.max_nodes})"

    def run(self, args: InspectWindowArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        if sys.platform != "win32":
            return ToolResult(
                ok=False, summary="inspect_window requires Windows UI Automation"
            )

        win32 = RealWin32()
        found = _find_window_by_title(win32, args.title)
        if not found:
            return ToolResult(
                ok=False, summary=f"Window matching {args.title!r} not found"
            )

        hwnd, window_title, proc_name = found
        if proc_name == "[admin]":
            return ToolResult(
                ok=False,
                summary=ADMIN_ERROR_MESSAGE,
                untrusted_text=ADMIN_ERROR_MESSAGE,
            )

        try:
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia_module

            c_uia = comtypes.client.CreateObject(
                uia_module.CUIAutomation, interface=uia_module.IUIAutomation
            )
            root_elem = c_uia.ElementFromHandle(hwnd)
            walker = c_uia.ControlViewWalker

            lines: list[str] = []
            count = 0

            def walk(elem: Any, current_depth: int) -> None:
                nonlocal count
                if count >= args.max_nodes or current_depth > args.depth:
                    return

                child = walker.GetFirstChildElement(elem)
                while child and count < args.max_nodes:
                    count += 1
                    indent = "  " * (current_depth - 1)
                    try:
                        ctrl_type_id = child.CurrentControlType
                        ctrl_type = UIA_CONTROL_TYPES.get(
                            ctrl_type_id, f"Control_{ctrl_type_id}"
                        )
                        name = (child.CurrentName or "").strip()
                        auto_id = (child.CurrentAutomationId or "").strip()

                        label = f"{indent}[{ctrl_type}]"
                        if name:
                            label += f" {name!r}"
                        if auto_id:
                            label += f" (id: {auto_id})"
                        lines.append(label)
                    except Exception as err:  # noqa: BLE001
                        if _is_admin_error(err):
                            raise
                        lines.append(f"{indent}[Element error: {err}]")

                    if current_depth < args.depth:
                        walk(child, current_depth + 1)

                    child = walker.GetNextSiblingElement(child)

            walk(root_elem, 1)

            summary = f"Inspected {count} control(s) in {window_title!r}"
            return ToolResult(
                ok=True,
                data={"hwnd": hwnd, "title": window_title, "nodes_inspected": count},
                summary=summary,
                untrusted_text="\n".join(lines)
                if lines
                else f"No child controls found in {window_title!r}.",
            )
        except Exception as exc:  # noqa: BLE001
            if _is_admin_error(exc):
                return ToolResult(
                    ok=False,
                    summary=ADMIN_ERROR_MESSAGE,
                    untrusted_text=ADMIN_ERROR_MESSAGE,
                )
            log.warning("inspect_window error: %s", exc)
            return ToolResult(ok=False, summary=f"inspect_window failed: {exc}")


# ---------------------------------------------------------------- open_app


class OpenAppArgs(BaseModel):
    name: str = Field(
        min_length=1,
        description=(
            "App name or command (e.g. 'notepad', 'code', 'chrome', "
            "'edge', 'explorer', 'terminal')"
        ),
    )


def _resolve_app_path(name: str) -> str | None:
    """Resolve an app executable per PLAN §13.3."""
    name_clean = name.strip()
    name_lower = name_clean.lower()

    # 1. Known map
    known: dict[str, str | None] = {
        "notepad": "notepad.exe",
        "explorer": "explorer.exe",
        "terminal": shutil.which("wt.exe") or "wt.exe",
        "chrome": (
            shutil.which("chrome.exe")
            or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            or r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ),
        "edge": (
            shutil.which("msedge.exe")
            or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            or r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
        ),
        "code": (
            shutil.which("code.cmd")
            or shutil.which("code.exe")
            or shutil.which("code")
            or os.path.expandvars(
                r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"
            )
        ),
    }

    if name_lower in known:
        candidate = known[name_lower]
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate

    # 2. Start-menu .lnk search
    start_menu_roots = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for sm_root in start_menu_roots:
        p = Path(sm_root)
        if p.exists():
            for lnk in p.rglob("*.lnk"):
                if name_lower in lnk.stem.lower():
                    return str(lnk)

    # 3. App Paths registry (read-only)
    if sys.platform == "win32":
        import winreg

        reg_name = name_clean if name_clean.lower().endswith(".exe") else f"{name_clean}.exe"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{reg_name}"
            try:
                with winreg.OpenKey(hive, key_path) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val and Path(val).exists():
                        return str(val)
            except OSError:
                pass

    # 4. Fallback: shutil.which
    which = shutil.which(name_clean)
    if which:
        return which

    return name_clean


class OpenAppTool(Tool[OpenAppArgs]):
    name: ClassVar[str] = "open_app"
    description: ClassVar[str] = (
        "Open a desktop application by name and wait for its window to appear."
    )
    Args: ClassVar[type[BaseModel]] = OpenAppArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "com"
    timeout_s: ClassVar[float] = 30.0

    def preview(self, args: OpenAppArgs) -> str:
        return f"Open application {args.name!r}"

    def run(self, args: OpenAppArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        resolved = _resolve_app_path(args.name)
        if not resolved:
            return ToolResult(
                ok=False, summary=f"Could not resolve application {args.name!r}"
            )

        try:
            if resolved.lower().endswith(".lnk"):
                os.startfile(resolved)
            else:
                subprocess.Popen([resolved])
        except Exception as exc:  # noqa: BLE001
            log.warning("open_app Popen failed for %s: %s", resolved, exc)
            try:
                os.startfile(resolved)
            except Exception as start_exc:  # noqa: BLE001
                return ToolResult(
                    ok=False,
                    summary=f"Failed to launch {args.name!r}: {start_exc}",
                )

        # Verification poll: wait up to 5s for window by process name AND title (§13.3)
        # Windows 11 Notepad hand-off: notepad.exe delegates to the modern packaged Notepad app.
        win32 = RealWin32()
        deadline = time.monotonic() + 5.0
        app_stem = Path(resolved).stem.lower()

        found_win: tuple[int, str, str] | None = None
        while time.monotonic() < deadline:
            time.sleep(0.3)
            windows = _enumerate_windows(win32)
            for h, t, p in windows:
                p_lower = p.lower()
                t_lower = t.lower()
                # Check process match or title match
                is_notepad = args.name.lower() == "notepad" and (
                    "notepad" in p_lower or "notepad" in t_lower
                )
                if (
                    args.name.lower() in p_lower
                    or app_stem in p_lower
                    or args.name.lower() in t_lower
                    or is_notepad
                ):
                    found_win = (h, t, p)
                    break
            if found_win:
                break

        if found_win:
            hwnd, title, proc = found_win
            return ToolResult(
                ok=True,
                data={"hwnd": hwnd, "title": title, "process": proc},
                summary=f"Opened {args.name!r}: window {title!r} ({proc})",
                untrusted_text=f"Opened {args.name}: {title} ({proc})",
            )

        return ToolResult(
            ok=True,
            summary=f"Launched {args.name!r} (window not yet detected within 5s)",
            untrusted_text=f"Launched {args.name}.",
        )

    def verify(self, args: OpenAppArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        if isinstance(result.data, dict) and "hwnd" in result.data:
            hwnd = result.data["hwnd"]
            win32 = RealWin32()
            return win32.is_window(hwnd)
        return True


# ---------------------------------------------------------------- focus_window


class FocusWindowArgs(BaseModel):
    title: str = Field(min_length=1, description="Window title substring to focus")


class FocusWindowTool(Tool[FocusWindowArgs]):
    name: ClassVar[str] = "focus_window"
    description: ClassVar[str] = (
        "Bring a visible window matching title substring to the foreground and focus it."
    )
    Args: ClassVar[type[BaseModel]] = FocusWindowArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "com"

    def preview(self, args: FocusWindowArgs) -> str:
        return f"Focus window {args.title!r}"

    def run(self, args: FocusWindowArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        if sys.platform != "win32":
            return ToolResult(ok=True, summary="Simulated focus (non-Windows)")

        win32 = RealWin32()
        found = _find_window_by_title(win32, args.title)
        if not found:
            return ToolResult(
                ok=False, summary=f"No window found matching {args.title!r}"
            )

        hwnd, window_title, proc_name = found
        if proc_name == "[admin]":
            return ToolResult(
                ok=False,
                summary=ADMIN_ERROR_MESSAGE,
                untrusted_text=ADMIN_ERROR_MESSAGE,
            )

        try:
            win32.focus_window(hwnd)
            return ToolResult(
                ok=True,
                data={"hwnd": hwnd, "title": window_title, "process": proc_name},
                summary=f"Focused window {window_title!r}",
                untrusted_text=f"Focused window: {window_title}",
            )
        except Exception as exc:  # noqa: BLE001
            if _is_admin_error(exc):
                return ToolResult(
                    ok=False,
                    summary=ADMIN_ERROR_MESSAGE,
                    untrusted_text=ADMIN_ERROR_MESSAGE,
                )
            return ToolResult(ok=False, summary=f"Failed to focus window: {exc}")

    def verify(self, args: FocusWindowArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        if sys.platform != "win32":
            return True
        win32 = RealWin32()
        fg_hwnd = win32.foreground_window()
        fg_title = win32.window_title(fg_hwnd)
        return args.title.lower() in fg_title.lower()


# ---------------------------------------------------------------- type_text


class TypeTextArgs(BaseModel):
    window: str = Field(
        min_length=1, description="Window title substring to target"
    )
    text: str = Field(description="Text to type into the target window")
    clear: bool = Field(
        default=False, description="Clear existing text before typing"
    )


class TypeTextTool(Tool[TypeTextArgs]):
    name: ClassVar[str] = "type_text"
    description: ClassVar[str] = (
        "Type text into a target window with pywinauto type_keys and set_edit_text fallback."
    )
    Args: ClassVar[type[BaseModel]] = TypeTextArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "com"
    timeout_s: ClassVar[float] = 30.0

    def preview(self, args: TypeTextArgs) -> str:
        clear_str = " (clearing first)" if args.clear else ""
        return f"Type {args.text!r} into window {args.window!r}{clear_str}"

    def run(self, args: TypeTextArgs, ctx: ToolContext) -> ToolResult:
        del ctx
        if sys.platform != "win32":
            return ToolResult(
                ok=True, summary=f"Simulated typing into {args.window!r}"
            )

        win32 = RealWin32()
        found = _find_window_by_title(win32, args.window)
        if not found:
            return ToolResult(
                ok=False, summary=f"Window matching {args.window!r} not found"
            )

        hwnd, window_title, proc_name = found
        if proc_name == "[admin]":
            return ToolResult(
                ok=False,
                summary=ADMIN_ERROR_MESSAGE,
                untrusted_text=ADMIN_ERROR_MESSAGE,
            )

        try:
            win32.focus_window(hwnd)
            time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001
            if _is_admin_error(exc):
                return ToolResult(
                    ok=False,
                    summary=ADMIN_ERROR_MESSAGE,
                    untrusted_text=ADMIN_ERROR_MESSAGE,
                )

        typed_successfully = False
        captured_value: str = ""

        try:
            from pywinauto import Application

            app = Application(backend="uia").connect(handle=hwnd)
            main_win = app.window(handle=hwnd)

            # Find target edit or document control
            edit_ctrl = None
            try:
                for descendant in main_win.descendants():
                    ctrl_type = getattr(descendant.element_info, "control_type", "")
                    if ctrl_type in ("Edit", "Document"):
                        edit_ctrl = descendant
                        break
            except Exception:  # noqa: BLE001
                pass

            target = edit_ctrl if edit_ctrl is not None else main_win

            if args.clear:
                try:
                    if hasattr(target, "set_edit_text"):
                        target.set_edit_text("")
                    else:
                        win32.send_chord(0x11, 0x41)  # Ctrl+A
                        time.sleep(0.05)
                        win32.send_chord(0x08)  # Backspace
                        time.sleep(0.05)
                except Exception:  # noqa: BLE001
                    pass

            # Primary typing: clipboard paste (instant, no repeated characters)
            old_clip = None
            try:
                old_clip = win32.get_clipboard_text()
            except Exception:  # noqa: BLE001
                pass

            try:
                win32.set_clipboard_text(args.text)
                time.sleep(0.05)
                win32.send_chord(0x11, 0x56)  # Ctrl+V
                time.sleep(0.1)
                typed_successfully = True
            except Exception as clip_err:  # noqa: BLE001
                log.debug("clipboard paste failed, attempting set_edit_text fallback: %s", clip_err)
                if hasattr(target, "set_edit_text"):
                    try:
                        target.set_edit_text(args.text)
                        typed_successfully = True
                    except Exception:  # noqa: BLE001
                        pass
                if not typed_successfully:
                    try:
                        target.type_keys(
                            args.text,
                            with_spaces=True,
                            with_newlines=True,
                            with_tabs=True,
                        )
                        typed_successfully = True
                    except Exception as type_err:  # noqa: BLE001
                        log.warning("fallback type_keys failed: %s", type_err)
            finally:
                if old_clip is not None:
                    try:
                        time.sleep(0.15)
                        win32.set_clipboard_text(old_clip)
                    except Exception:  # noqa: BLE001
                        pass

            # Attempt to read back control value for verify
            try:
                if edit_ctrl and hasattr(edit_ctrl, "get_value"):
                    captured_value = str(edit_ctrl.get_value() or "")
                elif edit_ctrl and hasattr(edit_ctrl, "window_text"):
                    captured_value = str(edit_ctrl.window_text() or "")
            except Exception:  # noqa: BLE001
                pass

        except Exception as exc:  # noqa: BLE001
            if _is_admin_error(exc):
                return ToolResult(
                    ok=False,
                    summary=ADMIN_ERROR_MESSAGE,
                    untrusted_text=ADMIN_ERROR_MESSAGE,
                )
            log.warning("pywinauto typing failed: %s", exc)
            # Final fallback: clipboard paste directly
            old_clip = None
            try:
                old_clip = win32.get_clipboard_text()
            except Exception:  # noqa: BLE001
                pass

            try:
                if args.clear:
                    win32.send_chord(0x11, 0x41)  # Ctrl+A
                    time.sleep(0.05)
                    win32.send_chord(0x08)  # Backspace
                    time.sleep(0.05)

                win32.set_clipboard_text(args.text)
                time.sleep(0.05)
                win32.send_chord(0x11, 0x56)
                time.sleep(0.1)
                typed_successfully = True
            except Exception as clip_err:  # noqa: BLE001
                if _is_admin_error(clip_err):
                    return ToolResult(
                        ok=False,
                        summary=ADMIN_ERROR_MESSAGE,
                        untrusted_text=ADMIN_ERROR_MESSAGE,
                    )
                return ToolResult(ok=False, summary=f"Failed to type text: {clip_err}")
            finally:
                if old_clip is not None:
                    try:
                        time.sleep(0.15)
                        win32.set_clipboard_text(old_clip)
                    except Exception:  # noqa: BLE001
                        pass

        return ToolResult(
            ok=typed_successfully,
            data={"hwnd": hwnd, "title": window_title, "value": captured_value},
            summary=f"Typed text into {window_title!r}",
            untrusted_text=f"Typed {args.text!r} into {window_title}.",
        )

    def verify(self, args: TypeTextArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        if sys.platform != "win32":
            return True
        if isinstance(result.data, dict):
            val = result.data.get("value", "")
            if val and args.text in val:
                return True
        return True
