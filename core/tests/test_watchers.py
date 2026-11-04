"""Tests for proactive watchers, log filters, signature normalisation, and debouncing."""

from pathlib import Path
from typing import Any

import pytest

from clickachu.context.capture import ContextStore
from clickachu.db.conn import Database
from clickachu.projects.profiles import ProjectStore
from clickachu.settings import SettingsStore
from clickachu.watchers.filters import (
    SuggestionManager,
    is_error_line,
    normalise_signature,
)
from clickachu.watchers.fullscreen import FullscreenWatcher
from clickachu.watchers.process_manager import ProcessManager

NODE_ECONNREFUSED_FIXTURE = """2026-11-02T11:21:22.456Z Error: connect ECONNREFUSED 127.0.0.1:6379
    at TCPConnectWrap.afterConnect [as oncomplete] (net.js:1146:16)
    at TCPConnectWrap.callbackTrampoline (internal/async_hooks.js:130:17)"""

PYTHON_TRACEBACK_FIXTURE = """Traceback (most recent call last):
  File "C:\\projects\\teamfuse\\worker.py", line 42, in <module>
    redis.ping()
ConnectionRefusedError: [Errno 111] Connection refused"""

EADDRINUSE_FIXTURE = "[11:21:22] Error: listen EADDRINUSE: address already in use :::3000"


def test_filter_matches_fixtures() -> None:
    # Node ECONNREFUSED
    first_node_line = NODE_ECONNREFUSED_FIXTURE.splitlines()[0]
    assert is_error_line(first_node_line)

    # Python Traceback
    first_py_line = PYTHON_TRACEBACK_FIXTURE.splitlines()[0]
    assert is_error_line(first_py_line)
    assert is_error_line(PYTHON_TRACEBACK_FIXTURE.splitlines()[-1])

    # EADDRINUSE
    assert is_error_line(EADDRINUSE_FIXTURE)

    # Non-error lines
    assert not is_error_line("Server ready and listening on port 8080")
    assert not is_error_line("GET /api/v1/health 200 OK 12ms")
    assert not is_error_line("[info] build succeeded in 3.4s")


def test_signature_normalisation_node_econnrefused() -> None:
    line1 = "2026-11-02T11:21:22.456Z Error: connect ECONNREFUSED 127.0.0.1:6379"
    line2 = "2026-11-02T12:00:00.000Z Error: connect ECONNREFUSED 127.0.0.1:6379"

    sig1 = normalise_signature("worker", line1)
    sig2 = normalise_signature("worker", line2)

    assert sig1 == sig2
    # Verify port 6379 is preserved and timestamp / IP numbers are stripped
    assert ":6379" in sig1
    assert "2026" not in sig1
    assert "ECONNREFUSED" in sig1


def test_signature_normalisation_python_traceback() -> None:
    line_win = '  File "C:\\projects\\teamfuse\\worker.py", line 42, in <module>'
    line_posix = '  File "/var/projects/teamfuse/worker.py", line 99, in <module>'

    sig_win = normalise_signature("teamfuse", line_win)
    sig_posix = normalise_signature("teamfuse", line_posix)

    # Both paths should reduce to worker.py, line numbers stripped
    assert "worker.py" in sig_win
    assert "worker.py" in sig_posix
    assert "42" not in sig_win
    assert "99" not in sig_posix
    assert sig_win == sig_posix


def test_signature_normalisation_eaddrinuse() -> None:
    line1 = "[11:21:22] Error: listen EADDRINUSE: address already in use :::3000"
    line2 = "[14:05:00] Error: listen EADDRINUSE: address already in use :::3000"

    sig1 = normalise_signature("web", line1)
    sig2 = normalise_signature("web", line2)

    assert sig1 == sig2
    assert ":::3000" in sig1 or ":3000" in sig1
    assert "11:21:22" not in sig1


@pytest.fixture
def test_setup(tmp_path: Path) -> tuple[Database, SuggestionManager, list[dict[str, Any]], Any]:
    db = Database(tmp_path / "test.db")
    contexts = ContextStore()
    projects = ProjectStore(db)
    settings = SettingsStore(db)
    proc_mgr = ProcessManager()
    events: list[dict[str, Any]] = []

    current_time = [1000.0]

    def fake_clock() -> float:
        return current_time[0]

    mgr = SuggestionManager(
        db=db,
        context_store=contexts,
        project_store=projects,
        settings_store=settings,
        process_manager=proc_mgr,
        emit=events.append,
        clock=fake_clock,
    )
    return db, mgr, events, current_time


def test_debounce_same_signature(test_setup: Any) -> None:
    _, mgr, events, clock = test_setup

    sig = "worker:Error: connect ECONNREFUSED :6379"

    # First occurrence triggers suggestion
    ev1 = mgr.create_suggestion("worker", sig, "worker error", "ECONNREFUSED")
    assert ev1 is not None
    assert len(events) == 1

    # Second occurrence within 5 minutes (e.g. after 60s) is debounced
    clock[0] += 60.0
    ev2 = mgr.create_suggestion("worker", sig, "worker error", "ECONNREFUSED")
    assert ev2 is None
    assert len(events) == 1

    # Third occurrence after 5 minutes (> 300s) triggers suggestion
    clock[0] += 250.0  # total elapsed: 310s > 300s
    ev3 = mgr.create_suggestion("worker", sig, "worker error", "ECONNREFUSED")
    assert ev3 is not None
    assert len(events) == 2


def test_max_3_pending_suggestions(test_setup: Any) -> None:
    db, mgr, events, _ = test_setup

    # Create 3 distinct suggestions
    for i in range(3):
        sig = f"proc_{i}:error"
        ev = mgr.create_suggestion(f"proc_{i}", sig, f"error {i}", "details")
        assert ev is not None
    assert len(events) == 3
    assert mgr.pending_count() == 3

    # 4th suggestion with a new signature is blocked by max pending
    ev4 = mgr.create_suggestion("proc_3", "proc_3:error", "error 3", "details")
    assert ev4 is None
    assert len(events) == 3

    # Dismiss one suggestion
    first_id = events[0]["id"]
    mgr.respond(first_id, "dismiss")
    assert mgr.pending_count() == 2

    # Now a 4th suggestion is allowed
    ev5 = mgr.create_suggestion("proc_3", "proc_3:error", "error 3", "details")
    assert ev5 is not None
    assert len(events) == 4
    assert mgr.pending_count() == 3


def test_investigate_creates_snapshot_with_redacted_logs(test_setup: Any) -> None:
    _, mgr, events, _ = test_setup

    # Add logs to process manager, including a secret pattern to verify redaction
    proc_mgr = mgr.process_manager
    managed = proc_mgr.start("TeamFuse", "echo started", cwd=".", port=None)
    managed.logs.append("Starting worker...")
    managed.logs.append("Connecting to redis with token sk-ant-api03-abcdef1234567890abcdef123456")
    managed.logs.append("Error: connect ECONNREFUSED 127.0.0.1:6379")

    ev = mgr.create_suggestion(
        "TeamFuse",
        "TeamFuse:ECONNREFUSED :6379",
        "TeamFuse error",
        "Error: connect ECONNREFUSED 127.0.0.1:6379",
    )
    assert ev is not None
    assert ev.context_id is not None

    snapshot = mgr.context_store.get(ev.context_id)
    assert snapshot is not None
    assert snapshot.process == "TeamFuse"
    assert snapshot.selection is not None
    # Secret must be redacted
    assert "sk-ant-api03" not in snapshot.selection
    assert "[REDACTED" in snapshot.selection
    assert "ECONNREFUSED" in snapshot.selection


@pytest.mark.asyncio
async def test_fullscreen_watcher() -> None:
    events: list[dict[str, Any]] = []
    is_fs = [False]

    def checker() -> bool:
        return is_fs[0]

    watcher = FullscreenWatcher(emit=events.append, checker=checker, poll_interval_s=0.01)

    # Initial check: not fullscreen
    await watcher.poll_once()
    assert not watcher.is_fullscreen

    # Simulate transition to fullscreen
    is_fs[0] = True
    await watcher.poll_once()

    assert watcher.is_fullscreen
    assert {"type": "orb.state", "state": "hidden"} in events

    # Simulate transition back
    is_fs[0] = False
    await watcher.poll_once()
    assert not watcher.is_fullscreen
    assert {"type": "orb.state", "state": "idle"} in events

    watcher.stop()
