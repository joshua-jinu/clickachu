"""The real Windows implementation of `context.selection.Win32`.

Imported lazily and only used on the COM worker thread (hard rule 6): the UIA client and the
clipboard are apartment-bound, and SendInput must not interleave with other automation.
"""

import ctypes
import logging
import os
import struct
import time
from ctypes import wintypes
from typing import Any

import psutil
import pywintypes
import win32clipboard
from comtypes import COMError

from clickachu.context.selection import FocusedElement

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
SW_RESTORE = 9
VK_MODIFIERS = (0x10, 0x11, 0x12)  # Shift, Ctrl, Alt
EXTENDED_VKS = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E})

UIA_TEXT_PATTERN_ID = 10014
UIA_MAX_TEXT = 200_000
CLIPBOARD_RETRIES = 20


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUT_UNION(ctypes.Union):
    # MOUSEINPUT is the largest member; without it sizeof(INPUT) is wrong and SendInput fails.
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ULONG_PTR]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _key_event(vk: int, up: bool) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0)
    event = INPUT(type=INPUT_KEYBOARD)
    event.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, 0)
    return event


class RealWin32:
    def __init__(self) -> None:
        self._uia: Any = None
        self._uia_module: Any = None
        self._own_pids = self._own_process_family()

    # ---- windows

    def foreground_window(self) -> int:
        return user32.GetForegroundWindow() or 0

    def is_window(self, hwnd: int) -> bool:
        return bool(user32.IsWindow(hwnd))

    def window_pid(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def process_name(self, pid: int) -> str:
        try:
            return psutil.Process(pid).name()
        except psutil.Error:
            return ""

    def window_title(self, hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def is_own_process(self, pid: int) -> bool:
        return pid in self._own_pids

    @staticmethod
    def _own_process_family() -> frozenset[int]:
        """Only this sidecar and its explicitly identified Electron main process are ours.

        Ancestors also include the user's editor/terminal when started with npm run dev.
        Treating those as our windows silently disables capture from VS Code and terminals.
        """
        pids = {os.getpid()}
        try:
            main_pid = int(os.environ.get("CLICKACHU_MAIN_PID", "0"))
            if main_pid > 0 and main_pid in {p.pid for p in psutil.Process().parents()}:
                pids.add(main_pid)
        except (ValueError, psutil.Error) as exc:
            log.warning("could not identify Clickachu main process: %s", exc)
        return frozenset(pids)

    def focus_window(self, hwnd: int) -> None:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        if user32.SetForegroundWindow(hwnd):
            return
        # Windows refuses foreground changes from background processes; attaching to the
        # current foreground thread's input queue makes the request count as theirs.
        foreground_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        this_thread = kernel32.GetCurrentThreadId()
        attached = bool(foreground_thread) and foreground_thread != this_thread
        if attached:
            attached = bool(user32.AttachThreadInput(this_thread, foreground_thread, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(this_thread, foreground_thread, False)

    # ---- keyboard

    def modifiers_down(self) -> bool:
        return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in VK_MODIFIERS)

    def send_chord(self, *vks: int) -> None:
        events = [_key_event(vk, up=False) for vk in vks]
        events += [_key_event(vk, up=True) for vk in reversed(vks)]
        array = (INPUT * len(events))(*events)
        sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
        if sent != len(events):
            log.warning(
                "SendInput sent %d of %d events (blocked by UIPI? trying keybd_event fallback)",
                sent,
                len(events),
            )
            for vk in vks:
                flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
                user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), flags, 0)
            for vk in reversed(vks):
                flags = KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0)
                user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), flags, 0)

    # ---- UIA

    def _ensure_uia(self) -> tuple[Any, Any]:
        if self._uia is None:
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as module

            self._uia_module = module
            self._uia = comtypes.client.CreateObject(
                module.CUIAutomation, interface=module.IUIAutomation
            )
        return self._uia, self._uia_module

    def focused_element(self) -> FocusedElement:
        try:
            uia, module = self._ensure_uia()
            element = uia.GetFocusedElement()
            class_name = element.CurrentClassName or ""
            if element.CurrentIsPassword:
                return FocusedElement(class_name, None, is_password=True)
            pattern = element.GetCurrentPattern(UIA_TEXT_PATTERN_ID)
            if not pattern:
                return FocusedElement(class_name, None)
            ranges = pattern.QueryInterface(module.IUIAutomationTextPattern).GetSelection()
            parts = [ranges.GetElement(i).GetText(UIA_MAX_TEXT) for i in range(ranges.Length)]
            return FocusedElement(class_name, "\n".join(parts))
        except (COMError, OSError, ValueError, AttributeError) as exc:
            log.debug("UIA focused-element probe failed: %s", exc)
            return FocusedElement()

    # ---- clipboard

    def clipboard_sequence(self) -> int:
        return user32.GetClipboardSequenceNumber()

    def get_clipboard_text(self) -> str | None:
        with _clipboard():
            if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return None
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            return data if isinstance(data, str) else None

    def set_clipboard_text(self, text: str | None) -> None:
        with _clipboard():
            win32clipboard.EmptyClipboard()
            if text is None:
                return
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            # Keep our temporary copies out of Win+V history and the cloud clipboard.
            for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
                win32clipboard.SetClipboardData(
                    win32clipboard.RegisterClipboardFormat(name), struct.pack("<I", 0)
                )

    # ---- time (overridable in tests)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.monotonic()


class _clipboard:
    """Open the clipboard, retrying while another process holds it."""

    def __enter__(self) -> None:
        for attempt in range(CLIPBOARD_RETRIES):
            try:
                win32clipboard.OpenClipboard()
                return
            except pywintypes.error:
                if attempt == CLIPBOARD_RETRIES - 1:
                    raise OSError("clipboard is locked by another process") from None
                time.sleep(0.01)

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, *_: object
    ) -> None:
        win32clipboard.CloseClipboard()
        # Callers handle OSError; pywintypes.error is not one.
        if isinstance(exc, pywintypes.error):
            raise OSError(f"clipboard error: {exc}") from exc
