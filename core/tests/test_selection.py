"""Selection capture and paste-back with Win32 mocked (PLAN §10)."""

import pytest

from clickachu.context.selection import (
    COPY,
    PASTE,
    TERMINAL_COPY,
    TERMINAL_PASTE,
    VK_RIGHT,
    FocusedElement,
    capture_selection,
    is_terminal,
    paste_back,
    restore_clipboard,
)
from tests.fake_win32 import FakeWin32


@pytest.mark.parametrize(
    ("process", "focused_class", "expected"),
    [
        ("WindowsTerminal.exe", "TermControl", True),
        ("conhost.exe", "", True),
        ("OpenConsole.exe", "", True),
        ("cmd.exe", "", True),
        ("powershell.exe", "", True),
        ("PWSH.EXE", "", True),
        ("wsl.exe", "", True),
        ("mintty.exe", "", True),
        ("Code.exe", "xterm-helper-textarea", True),
        ("Cursor.exe", "Xterm-screen", True),
        ("Code.exe", "monaco-editor", False),
        ("Code.exe", "", False),
        ("notepad.exe", "xterm", False),  # xterm check applies to the editors only
        ("chrome.exe", "Chrome_RenderWidgetHostHWND", False),
    ],
)
def test_terminal_detection(process: str, focused_class: str, expected: bool) -> None:
    assert is_terminal(process, focused_class) is expected


def test_uia_selection_sends_no_keys_and_leaves_clipboard() -> None:
    api = FakeWin32(focus=FocusedElement("RichEditD2DPT", "hello world"))
    raw = capture_selection(api)
    assert (raw.text, raw.method) == ("hello world", "uia")
    assert raw.process == "notepad.exe"
    assert api.chords == []
    assert api.clipboard == "previous clipboard"


def test_clipboard_fallback_copies_then_restores() -> None:
    api = FakeWin32(focus=FocusedElement("Edit", None), app_selection="from the app")
    raw = capture_selection(api)
    assert (raw.text, raw.method) == ("from the app", "clipboard")
    assert api.chords == [COPY]
    assert api.clipboard == "previous clipboard"


def test_uia_empty_selection_also_tries_clipboard() -> None:
    api = FakeWin32(focus=FocusedElement("Edit", ""), app_selection="late text")
    assert capture_selection(api).text == "late text"


def test_no_selection_leaves_clipboard_untouched() -> None:
    api = FakeWin32(focus=FocusedElement("Edit", None))
    seq = api.seq
    raw = capture_selection(api)
    assert (raw.text, raw.method) == (None, "none")
    assert api.chords == [COPY]
    assert (api.seq, api.clipboard) == (seq, "previous clipboard")


def test_previous_non_text_clipboard_is_cleared_not_left_with_selection() -> None:
    api = FakeWin32(app_selection="selected", clipboard=None)
    assert capture_selection(api).text == "selected"
    assert api.clipboard is None


@pytest.mark.parametrize("process", ["WindowsTerminal.exe", "powershell.exe", "cmd.exe"])
@pytest.mark.parametrize("selected", ["", "echo hi"])
def test_terminal_never_gets_plain_ctrl_c(process: str, selected: str) -> None:
    api = FakeWin32(process=process, terminal_app=True, app_selection=selected)
    raw = capture_selection(api)
    assert COPY not in api.chords
    assert api.chords == [TERMINAL_COPY]
    assert not api.interrupted
    assert raw.is_terminal
    assert raw.text == (selected or None)


@pytest.mark.parametrize("selected", ["", "npm ERR! code 1"])
def test_vscode_terminal_via_xterm_class(selected: str) -> None:
    api = FakeWin32(
        process="Code.exe",
        terminal_app=True,
        focus=FocusedElement("xterm-helper-textarea", None),
        app_selection=selected,
    )
    raw = capture_selection(api)
    assert raw.is_terminal
    assert api.chords == [TERMINAL_COPY]
    assert not api.interrupted
    assert raw.text == (selected or None)


def test_vscode_editor_uses_plain_copy() -> None:
    api = FakeWin32(
        process="Code.exe", focus=FocusedElement("monaco-editor", None), app_selection="const x"
    )
    raw = capture_selection(api)
    assert not raw.is_terminal
    assert api.chords == [COPY]
    assert raw.text == "const x"


def test_waits_for_modifiers_before_copying() -> None:
    api = FakeWin32(app_selection="late", modifiers_released_after=0.1)
    assert capture_selection(api).text == "late"
    assert 0.1 <= api.t < 0.3


def test_alt_q_capture_waits_for_a_normal_longer_key_release() -> None:
    api = FakeWin32(app_selection="selected source", modifiers_released_after=0.65)
    assert capture_selection(api).text == "selected source"
    assert api.chords == [COPY]


def test_async_editor_copy_has_time_to_reach_the_clipboard() -> None:
    class SlowCopy(FakeWin32):
        copy_pending = False

        def send_chord(self, *vks):
            self.chords.append(vks)
            self.copy_pending = vks == COPY

        def sleep(self, seconds):
            super().sleep(seconds)
            if self.copy_pending and self.t >= 0.4:
                self._set("delayed selected text")
                self.copy_pending = False

    api = SlowCopy()
    assert capture_selection(api).text == "delayed selected text"
    assert api.clipboard == "previous clipboard"


def test_foreground_change_during_uia_does_not_attach_another_apps_text() -> None:
    class Switching(FakeWin32):
        def focused_element(self):
            self.foreground = self.other_window
            return FocusedElement("Edit", "other app text")

    api = Switching()
    assert capture_selection(api).text is None
    assert api.chords == []


def test_modifiers_stuck_down_skips_clipboard_fallback() -> None:
    api = FakeWin32(app_selection="never", modifiers_released_after=99)
    raw = capture_selection(api)
    assert raw.text is None
    assert api.chords == []


def test_own_windows_are_never_sent_keys() -> None:
    api = FakeWin32(app_selection="mine", own=True)
    assert capture_selection(api).text is None
    assert api.chords == []


def test_password_fields_are_not_read() -> None:
    api = FakeWin32(focus=FocusedElement("Edit", "hunter2", is_password=True), app_selection="x")
    assert capture_selection(api).text is None
    assert api.chords == []


def test_foreground_change_during_wait_aborts_copy() -> None:
    class Switching(FakeWin32):
        def modifiers_down(self) -> bool:
            self.foreground = self.other_window  # user alt-tabbed while we waited
            return False

    api = Switching(app_selection="wrong window")
    assert capture_selection(api).text is None
    assert api.chords == []


def test_no_foreground_window() -> None:
    api = FakeWin32()
    api.foreground = 0
    raw = capture_selection(api)
    assert (raw.hwnd, raw.text, raw.method) == (0, None, "none")


# ---------------------------------------------------------------- paste back


def test_replace_pastes_then_restore_puts_clipboard_back() -> None:
    api = FakeWin32()
    outcome = paste_back(api, 1001, "new text", "replace", terminal=False)
    assert outcome.ok and outcome.restore is not None
    assert api.chords == [PASTE]
    assert api.pasted == ["new text"]
    restore_clipboard(api, outcome.restore)
    assert api.clipboard == "previous clipboard"


def test_insert_collapses_selection_first() -> None:
    api = FakeWin32()
    assert paste_back(api, 1001, "more", "insert", terminal=False).ok
    assert api.chords == [(VK_RIGHT,), PASTE]


def test_terminal_paste_uses_ctrl_shift_v_and_skips_right_arrow() -> None:
    api = FakeWin32(process="WindowsTerminal.exe")
    assert paste_back(api, 1001, "ls", "insert", terminal=True).ok
    assert api.chords == [TERMINAL_PASTE]


def test_focus_is_requested_when_another_window_is_foreground() -> None:
    api = FakeWin32()
    api.foreground = api.other_window
    assert paste_back(api, 1001, "x", "replace", terminal=False).ok
    assert api.focus_requests == [1001]


def test_focus_failure_leaves_text_on_clipboard_and_sends_nothing() -> None:
    api = FakeWin32(focusable=False)
    api.foreground = api.other_window
    outcome = paste_back(api, 1001, "copied for you", "replace", terminal=False)
    assert (outcome.ok, outcome.reason, outcome.restore) == (False, "focus", None)
    assert api.chords == []
    assert api.clipboard == "copied for you"


def test_closed_window_counts_as_focus_failure() -> None:
    api = FakeWin32()
    outcome = paste_back(api, 9999, "x", "replace", terminal=False)
    assert (outcome.ok, outcome.reason) == (False, "focus")
    assert api.clipboard == "x"


def test_restore_skipped_when_clipboard_changed_meanwhile() -> None:
    api = FakeWin32()
    outcome = paste_back(api, 1001, "x", "replace", terminal=False)
    assert outcome.restore is not None
    api.set_clipboard_text("user copied something else")
    restore_clipboard(api, outcome.restore)
    assert api.clipboard == "user copied something else"
