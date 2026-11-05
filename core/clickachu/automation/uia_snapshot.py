"""Bulk native UI Automation snapshots, including raw-view recovery across app frames."""

import logging
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class NativeControl:
    identity: tuple
    type: str
    text: str
    enabled: bool
    bounds: tuple
    context: str = ""
    frame: bool = False
    password: bool = False
    element: object = field(default=None, repr=False)


def _property(element, key, default=None):
    try:
        return element.GetCachedPropertyValue(key)
    except (OSError, ValueError):
        return default


def _capture(client, root, api, raw=False, limit=1800):
    from uiautomation import ControlTypeNames

    cache = client.CreateCacheRequest()
    cache.TreeScope = 7  # Element | Children | Descendants: one cross-process bulk request.
    cache.TreeFilter = client.RawViewCondition if raw else client.ControlViewCondition
    for prop in (30000, 30001, 30002, 30003, 30005, 30010, 30011, 30019, 30022, 30045):
        cache.AddProperty(prop)
    cached = root.BuildUpdatedCache(cache)
    queue = deque([(cached, "", False)])
    rows, visited = [], 0
    while queue and visited < limit:
        element, parent_name, parent_frame = queue.popleft()
        visited += 1
        control_type = ControlTypeNames.get(_property(element, 30003), "CustomControl")
        control_type = control_type.removesuffix("Control")
        name = _property(element, 30005, "") or ""
        password = bool(_property(element, 30019, False))
        value = _property(element, 30045, "")
        text = name if name else value if isinstance(value, str) else ""
        if password:
            text = "[password field]"
        frame = parent_frame or control_type in ("TitleBar", "MenuBar")
        runtime = _property(element, 30000, ())
        runtime = tuple(runtime) if isinstance(runtime, (tuple, list)) else ()
        identity = (runtime, _property(element, 30011, ""), control_type, name)
        box = _property(element, 30001, ())
        box = tuple(box) if isinstance(box, (tuple, list)) else ()
        offscreen = bool(_property(element, 30022, False))
        if element != cached and not offscreen and len(box) == 4:
            x, y, width, height = box
            if width > 0 and height > 0:
                rows.append(
                    NativeControl(
                        identity,
                        control_type,
                        " ".join(text.split())[:200],
                        bool(_property(element, 30010, True)),
                        (x, y, x + width, y + height),
                        parent_name,
                        frame,
                        password,
                        element,
                    )
                )
        context = (
            name[:100]
            if name and control_type in ("Group", "Pane", "Window", "ToolBar")
            else parent_name
        )
        children = element.GetCachedChildren()
        if children:
            for index in range(children.Length):
                queue.append((children.GetElement(index), context, frame))
    return rows, visited >= limit


def snapshot(window, deep=False):
    # Test/integration wrappers can supply their own children without native COM interfaces.
    if not getattr(getattr(window, "element_info", None), "element", None):
        return None
    import comtypes.client

    api = comtypes.client.GetModule("UIAutomationCore.dll")
    client = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=api.IUIAutomation
    )
    root = client.ElementFromHandle(window.handle)
    rows, truncated = _capture(client, root, api)
    content = [r for r in rows if not r.frame and r.text and r.type not in ("Window", "Pane")]
    backend = "UIA cached control view"
    if deep or len(content) < 5:
        raw, raw_truncated = _capture(client, root, api, raw=True)
        if len(raw) > len(rows):
            rows, truncated, backend = raw, raw_truncated, "UIA cached raw view"
    return rows, backend, truncated


def wrapper(element):
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    return UIAWrapper(UIAElementInfo(element))
