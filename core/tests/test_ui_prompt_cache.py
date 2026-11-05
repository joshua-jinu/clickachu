from copy import deepcopy

import pytest

from clickachu.automation.uia_snapshot import NativeControl
from clickachu.llm.local_actions import action_messages, action_schema
from clickachu.llm.ui_context import ui_context
from clickachu.tools.base import ToolContext, ToolResult
from clickachu.tools.computer import (
    DesktopActionArgs,
    DesktopActionTool,
    ObserveDesktopArgs,
    ObserveDesktopTool,
    invalidate_observations,
)
from clickachu.tools.registry import create_default_registry
from tests.test_live_execution import native_window as shared_native_window

native_window = shared_native_window


def test_current_full_ui_snapshot_supersedes_old_state_and_precedes_changing_task():
    messages = [
        {"role": "system", "content": "Safety constraints"},
        {"role": "user", "name": "current_task", "content": "Click Seven"},
        {"role": "tool", "_ui_window": 77, "content": "OLD_STATE c1 Seven"},
        {"role": "tool", "_ui_window": 88, "content": "Other window's evidence"},
        {"role": "tool", "_ui_window": 77, "content": "CURRENT_STATE c1 Seven c2 Eight"},
    ]
    result = action_messages(messages, [], False)
    assert "CURRENT_STATE" in result[2]["content"]
    assert "OLD_STATE" not in str(result)
    assert "Other window's evidence" in str(result)
    assert messages[1] in result
    assert result[-1]["role"] != "assistant"


def test_ui_result_remains_a_completed_tool_result_after_snapshot_reordering():
    messages = [
        {"role": "system", "content": "Safety constraints"},
        {"role": "user", "name": "current_task", "content": "Click Seven"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "opened",
                    "function": {"name": "open_app", "arguments": '{"name":"Example"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "opened",
            "_ui_window": 77,
            "content": "Current controls: c1 Seven",
        },
    ]
    result = action_messages(messages, [], False)
    assert any(m["content"].startswith("Tools returned UI state") for m in result)
    assert sum(len(str(m.get("content", ""))) + 32 for m in result) <= 9000


def test_unchanged_ui_prefix_survives_capture_id_and_control_order_changes():
    controls = [
        {"ref": "c1", "type": "Button", "text": "Seven"},
        {"ref": "c2", "type": "Button", "text": "Eight"},
    ]
    first = ToolResult(
        ok=True,
        summary="Read controls",
        data={"title": "Example", "observation_id": "old", "entries": controls},
    )
    second = ToolResult(
        ok=True,
        summary="Click sent",
        data={"title": "Example", "observation_id": "new", "entries": controls[::-1]},
    )
    a, b = (ui_context(r, include_observation_id=False) for r in (first, second))
    assert a.splitlines()[:3] == b.splitlines()[:3]
    assert "observation_id" not in a and "observation_id" not in b


def test_refs_survive_reordering_and_removed_ids_never_target_new_controls(
    db, native_window, monkeypatch
):
    import clickachu.automation.uia_snapshot as cached

    invalidate_observations("stable")
    rows = [
        NativeControl(((1,), "one", "Button", "One"), "Button", "One", True, (0, 0, 10, 10)),
        NativeControl(((2,), "two", "Button", "Two"), "Button", "Two", True, (0, 10, 10, 20)),
    ]
    monkeypatch.setattr(cached, "snapshot", lambda *args, **kw: (rows, "cached fixture", False))
    ctx = ToolContext(run_id="stable", step_id="1", snapshot=None, db=db)
    initial = ObserveDesktopTool().run(ObserveDesktopArgs(title="fixture", mode="uia"), ctx)
    original = {e["text"]: e["ref"] for e in initial.data["entries"]}
    rows.reverse()
    second = ObserveDesktopTool().run(ObserveDesktopArgs(title="fixture", mode="uia"), ctx)
    assert {e["text"]: e["ref"] for e in second.data["entries"]} == original
    rows[1] = NativeControl(
        ((3,), "three", "Button", "Three"), "Button", "Three", True, (0, 0, 10, 10)
    )
    third = ObserveDesktopTool().run(ObserveDesktopArgs(title="fixture", mode="uia"), ctx)
    changed = {e["text"]: e["ref"] for e in third.data["entries"]}
    assert changed["Two"] == original["Two"] and changed["Three"] != original["One"]
    stale = DesktopActionTool().run(
        DesktopActionArgs(
            observation_id=third.data["observation_id"], action="click", ref=original["One"]
        ),
        ctx,
    )
    assert not stale.ok and "Unknown control ref" in stale.summary


def test_empty_tree_still_allows_keys_without_invalid_empty_target_enums():
    tools = deepcopy(create_default_registry().schemas(names=["desktop_action"]))
    props = tools[0]["function"]["parameters"]["properties"]
    props["observation_id"]["enum"] = ["current"]
    props["ref"]["enum"] = [""]
    props.pop("x")
    props.pop("y")
    schema = action_schema(tools, True)
    actions = [
        v["properties"]["arguments"]["properties"]["action"]["const"] for v in schema["anyOf"]
    ]
    assert actions == ["keys", "scroll"]


def test_click_prefers_the_target_invoke_pattern_and_records_dispatch(db, native_window):
    from types import SimpleNamespace

    _, control = native_window
    control.element_info.control_type = "Button"
    control.iface_invoke = SimpleNamespace(Invoke=lambda: setattr(control, "invoked", True))
    ctx = ToolContext(run_id="invoke", step_id="1", snapshot=None, db=db)
    observed = ObserveDesktopTool().run(ObserveDesktopArgs(title="fixture", mode="uia"), ctx)
    result = DesktopActionTool().run(
        DesktopActionArgs(observation_id=observed.data["observation_id"], action="click", ref="c1"),
        ctx,
    )
    assert result.ok and control.invoked and not hasattr(control, "clicked")
    assert result.data["performed_action"]["input_dispatched"] is True
    assert result.data["performed_action"]["target"] == "Search"


def test_reinspection_keeps_the_identified_window_despite_duplicate_titles(
    db, native_window, monkeypatch
):
    import clickachu.tools.computer as computer

    ctx = ToolContext(run_id="duplicate-title", step_id="1", snapshot=None, db=db)
    first = ObserveDesktopTool().run(ObserveDesktopArgs(title="Store", mode="uia"), ctx)
    assert first.ok
    monkeypatch.setattr(
        computer, "target_window", lambda *args: pytest.fail("Ambiguous title lookup")
    )
    second = ObserveDesktopTool().run(ObserveDesktopArgs(title="Store", mode="uia"), ctx)
    assert second.ok and second.data["hwnd"] == first.data["hwnd"]
