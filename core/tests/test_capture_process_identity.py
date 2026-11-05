"""An editor launching Clickachu is a source app, not one of Clickachu's windows."""

import os
import sys
from types import SimpleNamespace

import pytest

from clickachu.context.selection import capture_selection
from tests.fake_win32 import FakeWin32

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows API adapter")


@pytest.mark.parametrize("main_pid", ["111", "", "invalid", "999"])
def test_source_editor_ancestor_is_not_mistaken_for_the_clickachu_window(monkeypatch, main_pid):
    from clickachu.automation import winapi

    monkeypatch.setenv("CLICKACHU_MAIN_PID", main_pid)
    monkeypatch.setattr(
        winapi.psutil,
        "Process",
        lambda: SimpleNamespace(
            parents=lambda: [SimpleNamespace(pid=111), SimpleNamespace(pid=4242)]
        ),
    )
    real = winapi.RealWin32()
    assert real.is_own_process(os.getpid())
    assert real.is_own_process(111) is (main_pid == "111")
    assert not real.is_own_process(4242)  # VS Code launching npm/electron.
    source = FakeWin32(process="Code.exe", app_selection="highlighted editor text")
    source.is_own_process = real.is_own_process
    assert capture_selection(source).text == "highlighted editor text"
