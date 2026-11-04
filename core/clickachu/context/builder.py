"""Turns a ContextSnapshot into the prompt's context block (PLAN §11, hard rule 3)."""

from clickachu.api.protocol import ContextSnapshot
from clickachu.llm.prompts import wrap_untrusted


def _line(value: str) -> str:
    # Window titles come from other apps: keep them on one line so they can't forge structure.
    return " ".join(value.split())


def context_block(
    snapshot: ContextSnapshot,
    *,
    include_selection: bool = True,
    max_selection_chars: int | None = None,
) -> str:
    lines = ["Context from the user's screen:", f"App: {_line(snapshot.app)}"]
    if snapshot.window_title:
        lines.append(f"Window title: {_line(snapshot.window_title)}")
    if snapshot.project:
        lines.append(f"Project: {_line(snapshot.project.name)}")
    if snapshot.is_terminal:
        lines.append("The app is a terminal.")
    if include_selection and snapshot.selection:
        sel = snapshot.selection
        truncated = snapshot.selection_truncated
        if max_selection_chars is not None and len(sel) > max_selection_chars:
            sel = sel[:max_selection_chars]
            truncated = True
        note = " (truncated)" if truncated else ""
        lines.append(f"Selected text{note}:")
        lines.append(wrap_untrusted("selection", sel))
    elif not include_selection:
        pass
    else:
        lines.append("No text is selected.")
    return "\n".join(lines)
