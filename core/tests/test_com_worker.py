import asyncio
import threading
import time

import pytest

from clickachu.automation.com_worker import ComWorker, ComWorkerStopped


@pytest.fixture
def worker():
    w = ComWorker(name="test-com")
    yield w
    w.shutdown()


async def test_runs_on_one_dedicated_daemon_thread(worker: ComWorker) -> None:
    ids = {await worker.run(threading.get_ident) for _ in range(5)}
    assert len(ids) == 1
    assert ids != {threading.get_ident()}
    assert worker._thread is not None and worker._thread.daemon


async def test_passes_args_and_returns_result(worker: ComWorker) -> None:
    assert await worker.run(pow, 2, 10) == 1024


async def test_exceptions_propagate(worker: ComWorker) -> None:
    def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await worker.run(boom)
    assert await worker.run(int, "7") == 7  # worker survives a failing job


async def test_jobs_run_in_submission_order(worker: ComWorker) -> None:
    order: list[int] = []
    await asyncio.gather(*(worker.run(order.append, i) for i in range(10)))
    assert order == list(range(10))


async def test_timeout_raises_and_skips_queued_job(worker: ComWorker) -> None:
    gate = threading.Event()
    ran: list[str] = []
    blocker = asyncio.create_task(worker.run(gate.wait, 5))
    await asyncio.sleep(0.05)
    with pytest.raises(TimeoutError):
        await worker.run(ran.append, "late", timeout=0.05)
    gate.set()
    await blocker
    await worker.run(int)  # drain
    assert ran == []


def test_shutdown_finishes_queued_jobs_and_rejects_new_ones() -> None:
    w = ComWorker(name="test-com-stop")
    gate = threading.Event()
    running = w.submit(gate.wait, 5)
    time.sleep(0.05)
    pending = w.submit(int)
    threading.Timer(0.1, gate.set).start()
    w.shutdown(timeout=2)
    assert running.result(1) is True
    assert pending.result(1) == 0  # queued before shutdown: still runs
    with pytest.raises(ComWorkerStopped):
        w.submit(int).result(1)
    assert w._thread is not None and not w._thread.is_alive()


def test_shutdown_without_start_is_noop() -> None:
    ComWorker().shutdown()
