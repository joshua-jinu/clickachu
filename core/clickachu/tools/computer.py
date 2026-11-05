"""Observed UI traversal: stable control refs, action/read-back, and bounded readiness waits."""

import base64
import io
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from clickachu.llm.capabilities import capabilities
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult, WorkerName

log = logging.getLogger(__name__)


@dataclass
class Observation:
    run_id: str
    hwnd: int
    rectangle: tuple[int, int, int, int]
    captured: float
    pid: int
    controls: dict[str, tuple] = field(default_factory=dict)
    title: str = ""
    elements: dict = field(default_factory=dict)
    ocr: dict = field(default_factory=dict)
    visual: bool = False


_observations: dict[str, Observation] = {}
_reference_maps: dict[tuple, dict] = {}


def reference_map(ctx, window, now):
    for key, state in list(_reference_maps.items()):
        if now - state["captured"] > 300:
            del _reference_maps[key]
    key = (ctx.run_id, window.handle, window.process_id())
    state = _reference_maps.setdefault(key, {"captured": now, "refs": {}, "c": 0, "o": 0})
    state["captured"] = now
    state["title"] = window.window_text()
    if len(state["refs"]) > 4096:
        state["refs"].clear()
    return state


def stable_ref(state, identity, prefix, seen):
    key = (prefix, identity)
    reusable = key not in seen and (prefix == "o" or bool(identity[0]))
    seen.add(key)
    if reusable and key in state["refs"]:
        return state["refs"][key]
    state[prefix] += 1
    ref = f"{prefix}{state[prefix]}"
    if reusable:
        state["refs"][key] = ref
    return ref


def current_observation(run_id):
    valid = [
        (key, obs)
        for key, obs in _observations.items()
        if obs.run_id == run_id and time.monotonic() - obs.captured <= 300
    ]
    return max(valid, key=lambda item: item[1].captured) if valid else None


def observation_error(args, run_id):
    observed = _observations.get(args.observation_id)
    if not observed or observed.run_id != run_id:
        return "Unknown or consumed observation. Observe again before acting."
    if time.monotonic() - observed.captured > 300:
        return "Observation expired. Observe again before acting."
    if args.ref and args.ref not in observed.controls:
        return "Unknown control ref. Observe again before acting."
    return None


def invalidate_observations(run_id: str) -> None:
    """A host script may change any app; its earlier control refs must not survive."""
    for key, observation in list(_observations.items()):
        if observation.run_id == run_id:
            _observations.pop(key, None)
    for key in list(_reference_maps):
        if key[0] == run_id:
            del _reference_maps[key]


def is_elevated(pid: int) -> bool:
    import win32api
    import win32con
    import win32security

    process = win32api.OpenProcess(0x1000, False, pid)
    try:
        token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
        try:
            return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
        finally:
            token.Close()
    finally:
        process.Close()


def target_window(title: str):
    from pywinauto import Desktop

    from clickachu.automation.winapi import RealWin32
    from clickachu.tools.desktop import _enumerate_windows

    # Media players and document editors often put only the current track/document in the
    # title. Resolve a unique process name as well; never pick the foreground app arbitrarily.
    visible = _enumerate_windows(RealWin32())
    query = title.strip().casefold()
    exact = [w for w in visible if w[1].casefold() == query]
    process = [w for w in visible if w[2].casefold().removesuffix(".exe") == query]
    partial = [w for w in visible if query in w[1].casefold()]
    windows = exact or process or partial
    if len(windows) != 1:
        raise ValueError(
            "Window title must identify exactly one visible window; list windows again."
        )
    return Desktop(backend="uia").window(handle=windows[0][0]).wrapper_object()


def context_window(title, ctx):
    """Use this task's identified window when another app window has the same title."""
    from pywinauto import Desktop

    scopes = sorted(
        [
            (key, state)
            for key, state in _reference_maps.items()
            if key[0] == ctx.run_id and time.monotonic() - state["captured"] <= 300
        ],
        key=lambda item: item[1]["captured"],
        reverse=True,
    )
    for (_, hwnd, pid), state in scopes:
        if title.casefold() not in state.get("title", "").casefold():
            continue
        window = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
        if window.process_id() != pid or not window.is_visible():
            raise ValueError("The identified window changed or closed; select the target again.")
        return window
    return target_window(title)


def rectangle(window):
    rect = window.rectangle()
    return rect.left, rect.top, rect.right, rect.bottom


def control_identity(control):
    info = control.element_info
    return (tuple(info.runtime_id or ()), info.automation_id, info.control_type, info.name)


def walk_controls(window):
    """Bound native traversal and omit repeated full descendants() scans."""
    queue = deque([(window, 0)])
    visited = 0
    while queue and visited < 500:
        parent, depth = queue.popleft()
        if depth > 20:
            continue
        try:
            children = parent.children()
        except (RuntimeError, OSError):
            continue
        for child in children:
            visited += 1
            if visited > 500:
                return
            yield child
            queue.append((child, depth + 1))


def action_scope(args, run_id: str) -> tuple[int, int] | None:
    observed = _observations.get(args.observation_id)
    if observed and observed.run_id == run_id:
        return observed.hwnd, observed.pid
    return None


def observed_control_label(args, run_id):
    observed = _observations.get(args.observation_id)
    if observed and observed.run_id == run_id and args.ref in observed.controls:
        return observed.controls[args.ref][3]
    return ""


def observe(
    window,
    ctx: ToolContext,
    include_image=False,
    query="",
    max_controls=60,
    mode="auto",
    deep=False,
) -> ToolResult:
    if is_elevated(window.process_id()):
        return ToolResult(ok=False, summary="Cannot control administrator windows.")
    now = time.monotonic()
    aliases, seen = reference_map(ctx, window, now), set()
    for key, old in list(_observations.items()):
        if now - old.captured > 300 or (old.run_id == ctx.run_id and old.hwnd == window.handle):
            del _observations[key]
    obs_id = uuid.uuid4().hex
    refs = {}
    lines = []
    entries = []
    candidates = []
    elements, ocr_targets = {}, {}
    backend, limited = "UIA traversal", False
    interactive = {
        "Button",
        "Edit",
        "ComboBox",
        "CheckBox",
        "RadioButton",
        "MenuItem",
        "TabItem",
        "Hyperlink",
        "ListItem",
        "DataItem",
        "TreeItem",
        "Slider",
    }
    # Keep actionable controls ahead of decorative text. Bound both traversal and prompt size.
    native = None
    hidden = []
    try:
        from clickachu.automation.uia_snapshot import snapshot

        native = snapshot(window, deep=deep)
    except Exception as exc:  # noqa: BLE001 - an unresponsive provider can fail in native COM.
        log.debug("Bulk UIA snapshot unavailable: %s", exc)
    if native:
        hidden = [row.bounds for row in native[0] if row.password]
    if native and mode != "ocr":
        rows, backend, limited = native
        terms = set(re.findall(r"\w+", query or ctx.user_text.casefold()))
        rows.sort(
            key=lambda row: (
                row.frame,
                not bool(terms & set(re.findall(r"\w+", row.text.casefold()))),
                row.type not in interactive,
            )
        )
        for row in rows:
            if query and query.casefold() not in (row.text + " " + row.context).casefold():
                continue
            if not row.text and row.type not in interactive:
                continue
            ref = stable_ref(aliases, row.identity, "c", seen)
            refs[ref], elements[ref] = row.identity, row.element
            entry = {
                "ref": ref,
                "type": row.type,
                "text": row.text,
                "enabled": row.enabled,
                "context": row.context,
                "bounds": row.bounds,
            }
            entries.append(entry)
            lines.append(
                f"{ref} [{row.type}] {row.text}"
                + (f" (in {row.context})" if row.context else "")
                + (" (disabled)" if not row.enabled else "")
            )
            if len(refs) >= max_controls:
                limited = len(rows) > len(refs)
                break
    for child in [] if native or mode == "ocr" else walk_controls(window):
        try:
            if not child.is_visible():
                continue
            info = child.element_info
            text = child.window_text() or info.name or ""
            # Password contents must never become prompt context.
            if getattr(info, "is_password", False):
                text = "[password field]"
                hidden.append(rectangle(child))
            text = " ".join(text.split())[:160]
            if not text and info.control_type not in interactive:
                continue
            if query and query.casefold() not in text.casefold():
                continue
            candidates.append((child, text, info.control_type))
            if len(candidates) >= 140 or time.monotonic() - now > 3:
                break
        except (RuntimeError, OSError):
            continue
    candidates.sort(key=lambda item: item[2] not in interactive)
    for child, text, control_type in candidates[:max_controls]:
        try:
            identity = control_identity(child)
            ref = stable_ref(aliases, identity, "c", seen)
            refs[ref] = identity
            entries.append(
                {"ref": ref, "type": control_type, "text": text, "enabled": child.is_enabled()}
            )
            lines.append(
                f"{ref} [{control_type}] {text}" + (" (disabled)" if not child.is_enabled() else "")
            )
        except (RuntimeError, OSError):
            continue
    content_controls = [e for e in entries if e["type"] in interactive and e["text"]]
    if mode == "ocr" or (mode == "auto" and len(content_controls) < 5):
        try:
            from clickachu.automation.ocr import words

            for value, bounds in words(window, hidden):
                if query and query.casefold() not in value.casefold():
                    continue
                if len(ocr_targets) >= max_controls:
                    break
                ref = stable_ref(aliases, (value, tuple(bounds)), "o", seen)
                refs[ref] = ((), "", "OCR", value)
                ocr_targets[ref] = (value, bounds)
                entries.append(
                    {"ref": ref, "type": "OCR", "text": value, "enabled": True, "bounds": bounds}
                )
                lines.append(f"{ref} [OCR text] {value}")
            backend += " + Windows OCR"
        except Exception as exc:  # noqa: BLE001
            lines.append(f"OCR unavailable: {type(exc).__name__}. Inspect a deeper UI subtree.")
            log.debug("OCR fallback unavailable: %s", exc)
    obs = Observation(
        ctx.run_id,
        window.handle,
        rectangle(window),
        now,
        window.process_id(),
        refs,
        window.window_text(),
        elements=elements,
        ocr=ocr_targets,
    )
    _observations[obs_id] = obs
    images = ()
    if (
        include_image
        and ctx.settings
        and capabilities(ctx.settings, ctx.settings.active_agent_model).images
    ):
        from clickachu.automation.ocr import capture

        image = capture(window, hidden)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        images = ("data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),)
        obs.visual = True
    coverage = (
        "Accessibility sample is limited; inspect with deep=true, query, "
        "or mode=ocr if the target is missing.\n"
        if limited or len(refs) < 9
        else ""
    )
    text = (
        f"Window: {obs.title}; hwnd={obs.hwnd}\nobservation_id={obs_id}\n"
        + f"Backend: {backend}\n"
        + coverage
        + "\n".join(lines)
    )
    return ToolResult(
        ok=True,
        summary=f"Observed {len(refs)} controls in {obs.title!r}.",
        data={
            "observation_id": obs_id,
            "title": obs.title,
            "hwnd": obs.hwnd,
            "controls": lines,
            "entries": entries,
            "bounds": obs.rectangle,
            "backend": backend,
            "limited": limited,
        },
        untrusted_text=text,
        images=images,
    )


class ObserveDesktopArgs(BaseModel):
    title: str = Field(min_length=1, description="Unique window title from list_windows/open_app")
    include_image: bool = Field(
        default=False, description="Only needed for visual/coordinate actions"
    )
    query: str = Field(default="", max_length=120, description="Optional control text filter")
    max_controls: int = Field(default=60, ge=1, le=100)
    mode: Literal["auto", "uia", "ocr"] = "auto"
    deep: bool = False


class ObserveDesktopTool(Tool[ObserveDesktopArgs]):
    name: ClassVar[str] = "observe_desktop"
    description: ClassVar[str] = (
        "Read a native window's live accessibility tree. Returns c1/c2 control refs and an "
        "observation_id for desktop_action. Request include_image only for visual fallback."
    )
    Args: ClassVar[type[BaseModel]] = ObserveDesktopArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "com"

    def run(self, args, ctx):
        return observe(
            context_window(args.title, ctx),
            ctx,
            args.include_image,
            args.query,
            args.max_controls,
            args.mode,
            args.deep,
        )


class DesktopActionArgs(BaseModel):
    observation_id: str = Field(min_length=1)
    action: Literal["click", "double_click", "fill", "keys", "scroll"]
    ref: str = Field(
        default="", description="Control ref from the latest observation; prefer over pixels"
    )
    text: str = Field(default="", max_length=20000, description="Text for fill")
    x: int | None = Field(
        default=None, ge=0, description="Window-relative pixels, visual fallback only"
    )
    y: int | None = Field(default=None, ge=0)
    keys: str = Field(default="", max_length=200, description="pywinauto syntax, e.g. {ENTER}, ^f")
    wheel: int = Field(default=0, ge=-10, le=10)
    submit: bool = Field(default=False, description="After fill, press Enter in that same editor")
    wait_for_text: str = Field(
        default="", max_length=200, description="Optional result text to wait for after the action"
    )
    wait_control_type: str = Field(
        default="", max_length=40, description="Optional UIA type, e.g. Text or ListItem"
    )
    wait_timeout_seconds: float = Field(default=8, ge=0.2, le=15)

    @model_validator(mode="after")
    def validate_submission(self):
        if self.submit and self.action != "fill":
            raise ValueError("submit is only valid with fill")
        if self.action == "keys" and not self.keys.strip():
            raise ValueError("keys requires a nonempty keys argument, e.g. keys='7'")
        if self.action == "fill" and not self.ref:
            raise ValueError("fill requires an observed editable control ref")
        if self.action in ("click", "double_click") and not self.ref:
            if self.x is None or self.y is None:
                raise ValueError("click requires an observed ref or both visual coordinates")
        if self.action == "scroll" and not self.wheel:
            raise ValueError("scroll requires a nonzero wheel argument")
        return self


class DesktopActionTool(Tool[DesktopActionArgs]):
    name: ClassVar[str] = "desktop_action"
    description: ClassVar[str] = (
        "Click/fill native controls by ref, press keys or scroll. Requires an observation_id. "
        "Returns a NEW observation: use its new refs for the next action. Fill verifies "
        "the editor value; clicks/keys require checking resulting controls for task completion."
    )
    Args: ClassVar[type[BaseModel]] = DesktopActionArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "com"
    timeout_s: ClassVar[float] = 25

    def preview(self, args):
        obs = _observations.get(args.observation_id)
        title = obs.title if obs else "expired window"
        target = args.ref or args.keys or (args.x, args.y)
        if obs and args.ref in obs.controls:
            target = f"{args.ref} ({obs.controls[args.ref][3]})"
        return (
            f"{args.action} in {title!r}: {target}"
            + (f"\nKeys: {args.keys}" if args.action == "keys" else "")
            + (f"\nText: {args.text}" if args.action == "fill" else "")
            + ("\nThen press Enter." if args.submit else "")
            + (f"\nWait for result: {args.wait_for_text}" if args.wait_for_text else "")
        )

    def run(self, args, ctx):
        obs = _observations.pop(args.observation_id, None)
        if not obs or obs.run_id != ctx.run_id:
            return ToolResult(ok=False, summary="Unknown or consumed observation. Observe again.")
        if time.monotonic() - obs.captured > 300:
            return ToolResult(ok=False, summary="Observation expired. Observe again before acting.")
        from pywinauto import Desktop, mouse
        from pywinauto.uia_defines import NoPatternInterfaceError

        from clickachu.automation.winapi import RealWin32

        window = Desktop(backend="uia").window(handle=obs.hwnd).wrapper_object()
        if window.process_id() != obs.pid or rectangle(window) != obs.rectangle:
            return ToolResult(ok=False, summary="Window identity or bounds changed. Observe again.")
        if is_elevated(obs.pid):
            return ToolResult(ok=False, summary="Cannot control administrator windows.")
        control = None
        ocr_target = None
        if args.ref:
            identity = obs.controls.get(args.ref)
            if identity is None:
                return ToolResult(ok=False, summary="Unknown control ref. Observe again.")
            if args.ref in obs.ocr:
                from clickachu.automation.ocr import same_target, words

                if args.action not in ("click", "double_click"):
                    return ToolResult(
                        ok=False, summary="OCR refs support clicks only; use UIA refs for editing."
                    )
                ocr_target = obs.ocr[args.ref]
                if not same_target(ocr_target, words(window)):
                    return ToolResult(
                        ok=False, summary="OCR target changed or moved. Observe again."
                    )
                matches = []
            elif args.ref in obs.elements:
                from clickachu.automation.uia_snapshot import wrapper

                candidate = wrapper(obs.elements[args.ref])
                matches = [candidate] if control_identity(candidate) == identity else []
            else:
                matches = [c for c in walk_controls(window) if control_identity(c) == identity]
            if not ocr_target and (
                len(matches) != 1 or not matches[0].is_visible() or not matches[0].is_enabled()
            ):
                return ToolResult(
                    ok=False, summary="Control changed or is not ready. Observe again."
                )
            control = matches[0] if matches else None
        if args.action == "fill" and (
            control is None or control.element_info.control_type not in ("Edit", "ComboBox")
        ):
            return ToolResult(ok=False, summary="Fill requires an observed editable control ref.")
        if args.action in ("click", "double_click") and control is None and not ocr_target:
            if (
                args.x is None
                or args.y is None
                or not (
                    args.x < obs.rectangle[2] - obs.rectangle[0]
                    and args.y < obs.rectangle[3] - obs.rectangle[1]
                )
            ):
                return ToolResult(
                    ok=False, summary="Use a control ref or coordinates inside the window."
                )
            if not obs.visual:
                return ToolResult(
                    ok=False,
                    summary="No observed pixels. Use a control ref or request OCR/image first.",
                )
        window.set_focus()
        if RealWin32().foreground_window() != obs.hwnd:
            return ToolResult(ok=False, summary="Window did not receive focus; no input sent.")
        if args.action == "fill":
            if control.element_info.control_type == "ComboBox":
                # Chromium search inputs can expose ComboBox rather than Edit. Only an
                # editable Value pattern is safe; never select an arbitrary list item.
                value = control.iface_value
                if value.CurrentIsReadOnly:
                    return ToolResult(ok=False, summary="This combo box is not editable.")
                value.SetValue(args.text)
                actual = value.CurrentValue
            else:
                control.set_edit_text(args.text)
                actual = control.get_value()
            if actual != args.text:
                return ToolResult(ok=False, summary="Editor value did not match requested text.")
            if args.submit:
                control.type_keys("{ENTER}", set_foreground=False)
        elif args.action in ("click", "double_click"):
            if ocr_target:
                _, (left, top, right, bottom) = ocr_target
                window.click_input(
                    coords=(int((left + right) / 2), int((top + bottom) / 2)),
                    double=args.action == "double_click",
                )
            elif control is not None:
                try:
                    invoke = control.iface_invoke if args.action == "click" else None
                except (AttributeError, NoPatternInterfaceError):
                    invoke = None
                if invoke is not None:
                    invoke.Invoke()
                else:
                    control.click_input(double=args.action == "double_click")
            else:
                window.click_input(coords=(args.x, args.y), double=args.action == "double_click")
        elif args.action == "keys":
            if not args.keys:
                return ToolResult(ok=False, summary="keys is required.")
            (control or window).type_keys(args.keys, set_foreground=False)
        else:
            rect = obs.rectangle
            mouse.scroll(
                coords=((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2), wheel_dist=args.wheel
            )
        if args.wait_for_text:
            refreshed = WaitForUITool().run(
                WaitForUIArgs(
                    title=window.window_text(),
                    text=args.wait_for_text,
                    control_type=args.wait_control_type,
                    timeout_seconds=args.wait_timeout_seconds,
                ),
                ctx,
            )
        else:
            time.sleep(0.15)
            refreshed = observe(window, ctx)
        import psutil

        try:
            process = psutil.Process(obs.pid).name()
        except psutil.Error:
            process = ""
        performed = {
            "action": args.action,
            "target": obs.controls.get(args.ref, (None, None, None, ""))[3],
            "window": obs.title,
            "process": process,
            "input_dispatched": True,
        }
        return ToolResult(
            ok=refreshed.ok,
            summary=f"{args.action} dispatched. {refreshed.summary}",
            data={**(refreshed.data or {}), "performed_action": performed},
            untrusted_text=refreshed.untrusted_text,
            images=refreshed.images,
        )


class WaitForUIArgs(BaseModel):
    title: str = Field(min_length=1)
    text: str = Field(min_length=1, description="Visible text/name expected in the window")
    timeout_seconds: float = Field(default=8, ge=0.2, le=15)
    disappear: bool = False
    control_type: str = Field(
        default="", max_length=40, description="UIA type filter; editors are excluded by default"
    )


class WaitForUITool(Tool[WaitForUIArgs]):
    name: ClassVar[str] = "wait_for_ui"
    description: ClassVar[str] = (
        "Wait for loading/results/dialog text to appear or disappear, polling real controls. "
        "Returns fresh controls on success, or a timeout with the last observed state."
    )
    Args: ClassVar[type[BaseModel]] = WaitForUIArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "com"
    timeout_s: ClassVar[float] = 20

    def preview(self, args):
        return f"Waiting for {args.text!r} to {'disappear' if args.disappear else 'appear'}"

    def run(self, args, ctx):
        deadline = time.monotonic() + args.timeout_seconds
        last = None
        while True:
            try:
                window = context_window(args.title, ctx)
                last = observe(window, ctx)
            except (RuntimeError, ValueError, OSError) as exc:
                # Launches and redraws may temporarily expose no target window.
                if time.monotonic() >= deadline:
                    return ToolResult(
                        ok=False,
                        summary=f"UI not ready: {exc}",
                        data=last.data if last else None,
                        untrusted_text=last.untrusted_text if last else None,
                    )
                time.sleep(0.25)
                continue
            # Match actual control contents, excluding the window caption/instruction metadata.
            found = any(
                args.text.casefold() in entry["text"].casefold()
                for entry in (last.data or {}).get("entries", [])
                if (
                    entry["type"].casefold() == args.control_type.casefold()
                    if args.control_type
                    else entry["type"] not in ("Edit", "ComboBox")
                )
            )
            if last.ok and found != args.disappear:
                condition = f"{args.text!r} {'disappeared' if args.disappear else 'appeared'}"
                return ToolResult(
                    ok=True,
                    summary=f"Verified UI condition: {condition}.",
                    data=last.data,
                    untrusted_text=last.untrusted_text,
                    images=last.images,
                )
            if time.monotonic() >= deadline:
                return ToolResult(
                    ok=False,
                    summary=f"Timed out waiting for {args.text!r}.",
                    data=last.data,
                    untrusted_text=last.untrusted_text,
                )
            time.sleep(0.25)
