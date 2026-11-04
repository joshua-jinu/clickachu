"""Scripted stand-in for `context.selection.Win32`: a foreground app, a clipboard, a clock."""

from clickachu.context.selection import COPY, PASTE, TERMINAL_COPY, TERMINAL_PASTE, FocusedElement


class FakeWin32:
    """`app_selection` is what the app would copy. Plain Ctrl+C in a terminal interrupts the
    foreground process instead (`interrupted`), which is exactly what PLAN §10 forbids.
    """

    def __init__(
        self,
        *,
        process: str = "notepad.exe",
        title: str = "notes.txt - Notepad",
        hwnd: int = 1001,
        focus: FocusedElement | None = None,
        app_selection: str = "",
        terminal_app: bool = False,
        clipboard: str | None = "previous clipboard",
        modifiers_released_after: float = 0.0,
        own: bool = False,
        focusable: bool = True,
    ) -> None:
        self.process = process
        self.title = title
        self.hwnd = hwnd
        self.focus = focus or FocusedElement()
        self.app_selection = app_selection
        self.terminal_app = terminal_app
        self.clipboard = clipboard
        self.seq = 100
        self.modifiers_released_after = modifiers_released_after
        self.own = own
        self.focusable = focusable
        self.foreground = hwnd
        self.other_window = 2002
        self.t = 0.0
        self.chords: list[tuple[int, ...]] = []
        self.pasted: list[str | None] = []
        self.interrupted = False
        self.focus_requests: list[int] = []

    # ---- windows
    def foreground_window(self) -> int:
        return self.foreground

    def is_window(self, hwnd: int) -> bool:
        return hwnd == self.hwnd

    def window_pid(self, hwnd: int) -> int:
        return 4242

    def process_name(self, pid: int) -> str:
        return self.process

    def window_title(self, hwnd: int) -> str:
        return self.title

    def is_own_process(self, pid: int) -> bool:
        return self.own

    def focus_window(self, hwnd: int) -> None:
        self.focus_requests.append(hwnd)
        if self.focusable:
            self.foreground = hwnd

    # ---- input
    def modifiers_down(self) -> bool:
        return self.t < self.modifiers_released_after

    def focused_element(self) -> FocusedElement:
        return self.focus

    def send_chord(self, *vks: int) -> None:
        self.chords.append(vks)
        if vks == COPY:
            if self.terminal_app:
                self.interrupted = True
            elif self.app_selection:
                self._set(self.app_selection)
        elif vks == TERMINAL_COPY:
            if self.app_selection:
                self._set(self.app_selection)
        elif vks in (PASTE, TERMINAL_PASTE):
            self.pasted.append(self.clipboard)

    # ---- clipboard
    def clipboard_sequence(self) -> int:
        return self.seq

    def get_clipboard_text(self) -> str | None:
        return self.clipboard

    def set_clipboard_text(self, text: str | None) -> None:
        self._set(text)

    def _set(self, text: str | None) -> None:
        self.clipboard = text
        self.seq += 1

    # ---- time
    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def now(self) -> float:
        return self.t
