"""Fullscreen foreground window watcher (PLAN §9.1, §16)."""

import asyncio
import ctypes
import logging
import sys
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from clickachu.automation.com_worker import run_com

log = logging.getLogger(__name__)

DESKTOP_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd"})
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _check_fullscreen_win32() -> bool:
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    hwnd = user32.GetForegroundWindow()
    if not hwnd or not user32.IsWindow(hwnd):
        return False

    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    cls_name = buf.value
    if cls_name in DESKTOP_CLASSES:
        return False

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    win_rect = (rect.left, rect.top, rect.right, rect.bottom)

    hmonitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not hmonitor:
        return False

    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(mi)):
        return False
    mon_rect = (mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom)

    return win_rect == mon_rect


class FullscreenWatcher:
    """Polls foreground window every 1 s on the COM worker and emits orb.state."""

    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        checker: Callable[[], bool] | None = None,
        poll_interval_s: float = 1.0,
    ) -> None:
        self.emit = emit
        self.checker = checker
        self.poll_interval_s = poll_interval_s
        self._task: asyncio.Task[None] | None = None
        self._is_fullscreen = False

    @property
    def is_fullscreen(self) -> bool:
        return self._is_fullscreen

    async def _check_once(self) -> bool:
        if self.checker is not None:
            return self.checker()
        try:
            return bool(await run_com(_check_fullscreen_win32, timeout=2.0))
        except Exception as exc:  # noqa: BLE001
            log.debug("fullscreen check error: %s", exc)
            return False

    async def poll_once(self) -> bool:
        fs = await self._check_once()
        if fs != self._is_fullscreen:
            self._is_fullscreen = fs
            new_state = "hidden" if fs else "idle"
            self.emit({"type": "orb.state", "state": new_state})
            log.debug("fullscreen state changed: %s -> %s", fs, new_state)
        return self._is_fullscreen

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.poll_interval_s)
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("error in fullscreen poll loop: %s", exc)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        if self._is_fullscreen:
            self._is_fullscreen = False
            self.emit({"type": "orb.state", "state": "idle"})
