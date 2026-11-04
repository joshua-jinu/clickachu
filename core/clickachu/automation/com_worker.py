"""The COM worker (PLAN §3 hard rule 6, §9 automation).

One daemon thread, initialised as a COM single-threaded apartment, runs every UIA / SendInput /
clipboard call. FastAPI handlers only ever `await run_com(...)`; they never touch those APIs.
"""

import asyncio
import ctypes
import logging
import queue
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

log = logging.getLogger(__name__)

COINIT_APARTMENTTHREADED = 0x2
DEFAULT_TIMEOUT_S = 5.0
_STOP = object()


class ComWorkerStopped(RuntimeError):
    """The worker is shut down (or shutting down); the job never ran."""


def _co_initialize() -> bool:
    if sys.platform != "win32":
        return False
    hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)  # type: ignore[attr-defined]
    # S_OK (0) and S_FALSE (1, already initialised) both need a matching CoUninitialize.
    if hr in (0, 1):
        return True
    log.error("CoInitializeEx failed: 0x%08x", hr & 0xFFFFFFFF)
    return False


def _co_uninitialize() -> None:
    if sys.platform == "win32":
        ctypes.windll.ole32.CoUninitialize()  # type: ignore[attr-defined]


class ComWorker:
    def __init__(self, name: str = "clickachu-com") -> None:
        self._name = name
        self._jobs: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False

    @property
    def thread_id(self) -> int | None:
        return self._thread.ident if self._thread else None

    def start(self) -> None:
        with self._lock:
            if self._stopped:
                raise ComWorkerStopped("COM worker was shut down")
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
                self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any) -> "Future[Any]":
        future: Future[Any] = Future()
        with self._lock:
            if self._stopped:
                future.set_exception(ComWorkerStopped("COM worker was shut down"))
                return future
        self.start()
        self._jobs.put((fn, args, future))
        return future

    async def run(
        self, fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT_S
    ) -> Any:
        """Run `fn(*args)` on the COM thread. A job that times out before starting never runs."""
        future = self.submit(fn, *args)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
        except (TimeoutError, asyncio.CancelledError):
            future.cancel()
            raise

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread = self._thread
        if thread is None:
            return
        self._jobs.put(_STOP)
        thread.join(timeout)
        if thread.is_alive():
            log.warning("COM worker did not stop within %.1fs (job still running?)", timeout)

    def _loop(self) -> None:
        initialised = _co_initialize()
        try:
            while True:
                job = self._jobs.get()
                if job is _STOP:
                    break
                fn, args, future = job
                # Cancelled while queued (caller timed out): skip it.
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(fn(*args))
                except BaseException as exc:  # noqa: BLE001 - forwarded to the awaiting caller
                    future.set_exception(exc)
        finally:
            self._drain()
            if initialised:
                _co_uninitialize()

    def _drain(self) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            if job is not _STOP:
                job[2].set_exception(ComWorkerStopped("COM worker was shut down"))


_worker = ComWorker()


def get_worker() -> ComWorker:
    return _worker


async def run_com(fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT_S) -> Any:
    return await _worker.run(fn, *args, timeout=timeout)


def shutdown_com_worker() -> None:
    _worker.shutdown()
