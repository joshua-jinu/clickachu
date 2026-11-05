"""Regressions for fabricated execution, raw output delivery, and observed UI traversal."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import RunMessage
from clickachu.llm.provider import Done, TextDelta
from clickachu.tools.base import ToolContext
from clickachu.tools.computer import (
    DesktopActionArgs,
    DesktopActionTool,
    ObserveDesktopArgs,
    ObserveDesktopTool,
    WaitForUIArgs,
    WaitForUITool,
)
from clickachu.tools.execution import read_bounded
from clickachu.tools.process_tools import (
    ListProcessesTool,
    process_list_requested,
)
from clickachu.tools.registry import ToolRegistry
from tests.fakes import FakeProvider, FakeSecretStore


@pytest.mark.parametrize(
    "text",
    [
        "can you check what all processes is my pc running?",
        "can you check all running processes and display to me",
        "show running apps",
    ],
)
def test_process_requests_are_live_inspection(text):
    assert process_list_requested(text)
    assert not process_list_requested("how do I list running processes?")


async def test_process_list_is_direct_complete_and_needs_no_model(settings_store, monkeypatch):
    import psutil

    fixtures = [
        SimpleNamespace(
            info={
                "pid": i,
                "name": f"fixture-{i}",
                "memory_info": SimpleNamespace(rss=1048576),
                "cpu_times": SimpleNamespace(user=2, system=1),
            }
        )
        for i in range(400)
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda *args, **kw: fixtures)
    registry = ToolRegistry()
    registry.register(ListProcessesTool())
    provider = FakeProvider([TextDelta("fabricated"), Done()])
    events = []

    async def emit(event):
        events.append(event)

    agent = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(key=None),
        provider_factory=provider.factory,
        registry=registry,
    )
    await agent.handle_run(RunMessage(run_id="process-test", text="check all running processes"))
    assert not provider.calls
    final = next(e for e in events if e.type == "run.finished")
    assert final.ok and "fixture-399" in final.text and "400 running processes" in final.text
    assert final.metrics.model_calls == 0
    assert any(e.type == "step.output" for e in events)


async def test_unexecuted_success_claim_never_reaches_chat(settings_store):
    from clickachu.tools.memory_tools import RecallTool

    registry = ToolRegistry()
    registry.register(RecallTool())
    provider = FakeProvider(
        [TextDelta("✅ Executed in PowerShell as Admin.\n```powershell\nGet-Process\n```"), Done()]
    )
    events = []

    async def emit(event):
        events.append(event)

    agent = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=provider.factory,
        registry=registry,
    )
    await agent.handle_run(RunMessage(run_id="fake", text="open Microsoft Store", mode="agent"))
    assert not any(e.type == "token" for e in events)
    assert any(e.type == "run.error" and "No action was executed" in e.message for e in events)
    assert len(provider.calls) == 2


async def test_output_arrives_before_process_eof_and_redacts_split_secrets():
    stream = asyncio.StreamReader()
    pieces = []

    async def output(text):
        pieces.append(text)

    task = asyncio.create_task(read_bounded(stream, output))
    stream.feed_data(b"first line\napi_")
    await asyncio.sleep(0)
    assert pieces == ["first line\n"] and not task.done()
    stream.feed_data(b"key=private-value\n")
    await asyncio.sleep(0)
    assert "private-value" not in "".join(pieces)
    stream.feed_eof()
    await task
    assert "[REDACTED]" in "".join(pieces)


@pytest.fixture
def native_window(monkeypatch):
    import clickachu.automation.ocr as ocr
    import clickachu.tools.computer as computer

    monkeypatch.setattr(ocr, "words", lambda *args: [])

    class Control:
        def __init__(self, name, control_type, identity):
            self.value = name
            self.element_info = SimpleNamespace(
                name=name,
                control_type=control_type,
                runtime_id=[identity],
                automation_id=str(identity),
                is_password=False,
            )

        def window_text(self):
            return self.value

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def children(self):
            return []

        def set_edit_text(self, text):
            self.value = text

        def get_value(self):
            return self.value

        def type_keys(self, keys, **kw):
            if keys == "{ENTER}":
                window.results = True

        def click_input(self, **kw):
            self.clicked = True

    edit = Control("Search", "Edit", 1)
    result = Control("Minecraft search results", "Text", 2)

    class Window:
        handle = 77
        results = False

        def process_id(self):
            return 88

        def window_text(self):
            return "Microsoft Store fixture"

        def rectangle(self):
            return SimpleNamespace(left=0, top=0, right=400, bottom=600)

        def children(self):
            return [edit, result] if self.results else [edit]

        def descendants(self):
            return self.children()

        def set_focus(self):
            pass

        def is_visible(self):
            return True

    window = Window()
    monkeypatch.setattr(computer, "target_window", lambda title: window)
    monkeypatch.setattr(computer, "is_elevated", lambda pid: False)
    monkeypatch.setitem(
        sys.modules,
        "pywinauto.uia_defines",
        SimpleNamespace(
            NoPatternInterfaceError=RuntimeError,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(
            Desktop=lambda **kw: SimpleNamespace(
                window=lambda **kw: SimpleNamespace(wrapper_object=lambda: window)
            ),
            mouse=SimpleNamespace(scroll=lambda **kw: None),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "clickachu.automation.winapi",
        SimpleNamespace(RealWin32=lambda: SimpleNamespace(foreground_window=lambda: 77)),
    )
    return window, edit


def test_cached_controls_are_used_without_rescanning_the_tree(db, native_window, monkeypatch):
    import clickachu.automation.uia_snapshot as cached
    import clickachu.tools.computer as computer

    window, edit = native_window
    row = cached.NativeControl(
        computer.control_identity(edit),
        "Edit",
        "Search",
        True,
        (0, 0, 100, 30),
        element=edit,
    )
    monkeypatch.setattr(cached, "snapshot", lambda *args, **kw: ([row], "cached fixture", False))
    monkeypatch.setattr(cached, "wrapper", lambda element: element)
    monkeypatch.setattr(computer, "walk_controls", lambda *args: pytest.fail("Unexpected rescan"))
    ctx = ToolContext(run_id="cached", step_id="1", snapshot=None, db=db)
    observed = ObserveDesktopTool().run(ObserveDesktopArgs(title="fixture"), ctx)
    result = DesktopActionTool().run(
        DesktopActionArgs(
            observation_id=observed.data["observation_id"],
            action="fill",
            ref="c1",
            text="Verified edit",
        ),
        ctx,
    )
    assert result.ok and edit.value == "Verified edit"


def test_ocr_rejects_a_changed_target_without_sending_input(db, native_window, monkeypatch):
    import clickachu.automation.ocr as ocr

    window, _ = native_window
    monkeypatch.setattr(ocr, "words", lambda *args: [("Play", (10, 10, 60, 30))])
    ctx = ToolContext(run_id="ocr", step_id="1", snapshot=None, db=db)
    observed = ObserveDesktopTool().run(ObserveDesktopArgs(title="fixture", mode="ocr"), ctx)
    monkeypatch.setattr(ocr, "words", lambda *args: [("Delete", (10, 10, 60, 30))])
    result = DesktopActionTool().run(
        DesktopActionArgs(observation_id=observed.data["observation_id"], action="click", ref="o1"),
        ctx,
    )
    assert not result.ok and "changed" in result.summary


def test_text_ocr_does_not_authorize_guessed_pixel_coordinates(db, native_window, monkeypatch):
    import clickachu.automation.ocr as ocr

    monkeypatch.setattr(ocr, "words", lambda *args: [("Play", (10, 10, 60, 30))])
    ctx = ToolContext(run_id="pixels", step_id="1", snapshot=None, db=db)
    observed = ObserveDesktopTool().run(ObserveDesktopArgs(title="fixture", mode="ocr"), ctx)
    result = DesktopActionTool().run(
        DesktopActionArgs(
            observation_id=observed.data["observation_id"], action="click", x=20, y=20
        ),
        ctx,
    )
    assert not result.ok and "No observed pixels" in result.summary


def test_window_resolution_accepts_unique_process_despite_changing_title(monkeypatch):
    import clickachu.tools.computer as computer
    import clickachu.tools.desktop as desktop

    monkeypatch.setattr(
        desktop,
        "_enumerate_windows",
        lambda *args: [
            (51, "An ever-changing song title", "ExamplePlayer.exe"),
            (52, "Unrelated document", "Editor.exe"),
        ],
    )
    monkeypatch.setitem(
        sys.modules, "clickachu.automation.winapi", SimpleNamespace(RealWin32=lambda: None)
    )
    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(
            Desktop=lambda **kw: SimpleNamespace(
                window=lambda **kw: SimpleNamespace(wrapper_object=lambda: kw["handle"])
            ),
        ),
    )
    assert computer.target_window("ExamplePlayer") == 51
    monkeypatch.setattr(
        desktop,
        "_enumerate_windows",
        lambda *args: [
            (51, "Track one", "ExamplePlayer.exe"),
            (53, "Track two", "ExamplePlayer.exe"),
        ],
    )
    with pytest.raises(ValueError, match="exactly one"):
        computer.target_window("ExamplePlayer")


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "keys", "text": "7"},
        {"action": "fill", "text": "Example"},
        {"action": "click"},
        {"action": "scroll"},
    ],
)
def test_incomplete_ui_commands_fail_argument_validation(arguments):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DesktopActionArgs(observation_id="observed", **arguments)


def test_native_search_traverses_refs_and_returns_fresh_observations(db, native_window):
    ctx = ToolContext(run_id="ui", step_id="1", snapshot=None, db=db)
    initial = ObserveDesktopTool().run(ObserveDesktopArgs(title="Store"), ctx)
    filled = DesktopActionTool().run(
        DesktopActionArgs(
            observation_id=initial.data["observation_id"], action="fill", ref="c1", text="Minecraft"
        ),
        ctx,
    )
    assert filled.ok and native_window[1].value == "Minecraft"
    assert filled.data["observation_id"] != initial.data["observation_id"]
    assert (
        not DesktopActionTool()
        .run(
            DesktopActionArgs(
                observation_id=initial.data["observation_id"], action="keys", keys="{ENTER}"
            ),
            ctx,
        )
        .ok
    )
    searched = DesktopActionTool().run(
        DesktopActionArgs(
            observation_id=filled.data["observation_id"], action="keys", ref="c1", keys="{ENTER}"
        ),
        ctx,
    )
    assert searched.ok and "Minecraft search results" in searched.untrusted_text
    ready = WaitForUITool().run(WaitForUIArgs(title="Store", text="Minecraft search results"), ctx)
    assert ready.ok


def test_action_schema_binds_current_refs_and_excludes_unobserved_actions(db, native_window):
    from clickachu.tools.computer import invalidate_observations
    from clickachu.tools.registry import create_default_registry

    registry = create_default_registry()
    schemas = registry.hybrid_schemas("open Example App")
    assert "desktop_action" not in {
        s["function"]["name"] for s in registry.bind_ui_state(schemas, "bound")
    }
    ctx = ToolContext(run_id="bound", step_id="1", snapshot=None, db=db)
    observed = ObserveDesktopTool().run(ObserveDesktopArgs(title="Example App"), ctx)
    bound = registry.bind_ui_state(schemas, "bound")
    action = next(s for s in bound if s["function"]["name"] == "desktop_action")
    properties = action["function"]["parameters"]["properties"]
    assert properties["observation_id"]["enum"] == [observed.data["observation_id"]]
    assert properties["ref"]["enum"] == ["", "c1"]
    assert (
        "enum"
        not in next(s for s in schemas if s["function"]["name"] == "desktop_action")["function"][
            "parameters"
        ]["properties"]["observation_id"]
    )
    invalidate_observations("bound")
    assert "desktop_action" not in {
        s["function"]["name"] for s in registry.bind_ui_state(schemas, "bound")
    }


async def test_invalid_ui_reference_is_rejected_before_requesting_approval(settings_store):
    from clickachu.api.protocol import SettingsUpdate
    from clickachu.llm.provider import ToolCall

    settings_store.update(SettingsUpdate(agent_max_steps=1))
    registry = ToolRegistry()
    registry.register(DesktopActionTool())
    provider = FakeProvider(
        [
            ToolCall(
                "bad", "desktop_action", '{"observation_id":"invented","action":"click","ref":"c1"}'
            ),
            Done(),
        ]
    )
    events = []

    async def emit(event):
        events.append(event)

    agent = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        registry=registry,
        provider_factory=provider.factory,
    )
    await agent.handle_run(
        RunMessage(run_id="invalid", text="click the requested button", mode="agent")
    )
    assert not any(e.type == "approval.request" for e in events)
    assert not any(e.type == "step.started" for e in events)
    assert next(e for e in events if e.type == "run.finished").ok is False


def test_native_wait_reports_timeout_with_actual_controls(db, native_window):
    ctx = ToolContext(run_id="ui", step_id="1", snapshot=None, db=db)
    result = WaitForUITool().run(
        WaitForUIArgs(title="Store", text="not present", timeout_seconds=0.2), ctx
    )
    assert not result.ok and "Search" in result.untrusted_text


def test_native_search_can_fill_submit_and_wait_in_one_call(db, native_window):
    ctx = ToolContext(run_id="combined", step_id="1", snapshot=None, db=db)
    initial = ObserveDesktopTool().run(ObserveDesktopArgs(title="Store"), ctx)
    result = DesktopActionTool().run(
        DesktopActionArgs(
            observation_id=initial.data["observation_id"],
            action="fill",
            ref="c1",
            text="Minecraft",
            submit=True,
            wait_for_text="Minecraft",
            wait_control_type="Text",
        ),
        ctx,
    )
    assert result.ok and native_window[0].results
    assert "Minecraft search results" in result.untrusted_text


def test_native_search_supports_editable_combobox_without_selecting_items(db, native_window):
    edit = native_window[1]
    edit.element_info.control_type = "ComboBox"

    class Value:
        CurrentIsReadOnly = False

        @property
        def CurrentValue(self):
            return edit.value

        def SetValue(self, value):
            edit.value = value

    edit.iface_value = Value()
    ctx = ToolContext(run_id="combo", step_id="1", snapshot=None, db=db)
    initial = ObserveDesktopTool().run(ObserveDesktopArgs(title="Spotify"), ctx)
    result = DesktopActionTool().run(
        DesktopActionArgs(
            observation_id=initial.data["observation_id"],
            action="fill",
            ref="c1",
            text="A song",
        ),
        ctx,
    )
    assert result.ok and edit.value == "A song"


@pytest.mark.parametrize("control_type", ["Edit", "ComboBox"])
def test_wait_does_not_mistake_editor_value_for_results(db, native_window, control_type):
    native_window[1].value = "Minecraft"
    native_window[1].element_info.control_type = control_type
    ctx = ToolContext(run_id="waiting", step_id="1", snapshot=None, db=db)
    result = WaitForUITool().run(
        WaitForUIArgs(
            title="Store",
            text="Minecraft",
            timeout_seconds=0.2,
        ),
        ctx,
    )
    assert not result.ok


def test_wait_can_target_an_editor_value_explicitly(db, native_window):
    native_window[1].value = "Minecraft"
    ctx = ToolContext(run_id="editor", step_id="1", snapshot=None, db=db)
    result = WaitForUITool().run(
        WaitForUIArgs(
            title="Store",
            text="Minecraft",
            control_type="Edit",
            timeout_seconds=0.2,
        ),
        ctx,
    )
    assert result.ok


def test_wait_handles_a_window_that_appears_during_loading(db, native_window, monkeypatch):
    import clickachu.tools.computer as computer

    attempts = []

    def target(title):
        attempts.append(title)
        if len(attempts) == 1:
            raise ValueError("Window not yet available")
        native_window[0].results = True
        return native_window[0]

    monkeypatch.setattr(computer, "target_window", target)
    ctx = ToolContext(run_id="loading", step_id="1", snapshot=None, db=db)
    assert WaitForUITool().run(WaitForUIArgs(title="Store", text="Minecraft"), ctx).ok
    assert len(attempts) == 2


def test_ui_tasks_can_discover_code_for_method_switching_without_always_loading_it(db):
    from clickachu.tools.registry import create_default_registry

    registry = create_default_registry()
    tools = registry.hybrid_schemas("open the app and prepare a report")
    assert {"discover_tools", "observe_desktop", "desktop_action"} <= {
        item["function"]["name"] for item in tools
    }
    assert "execute_code" not in {item["function"]["name"] for item in tools}
    discovery = registry.get("discover_tools")
    result = discovery.run(
        discovery.Args(query="execute_code"),
        ToolContext(run_id="discovery", step_id="1", snapshot=None, db=db),
    )
    assert any(s["function"]["name"] == "execute_code" for s in result.data["schemas"])
    explicit = registry.hybrid_schemas("open the app and run a Python script")
    assert "execute_code" in {s["function"]["name"] for s in explicit}


async def test_tool_discovery_does_not_count_as_executing_a_task(settings_store):
    from clickachu.llm.provider import ToolCall
    from clickachu.tools.registry import create_default_registry
    from tests.fakes import QueueProvider

    provider = QueueProvider(
        [
            [ToolCall("discovery", "discover_tools", '{"query":"desktop"}'), Done()],
            [TextDelta("Successfully opened the app."), Done()],
        ]
    )
    events = []

    async def emit(event):
        events.append(event)

    agent = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=provider.factory,
        registry=create_default_registry(),
    )
    await agent.handle_run(RunMessage(run_id="discovery-only", text="open an app", mode="agent"))
    assert not any(e.type == "token" for e in events)
    assert any(e.type == "run.error" and "No action was executed" in e.message for e in events)


async def test_duplicate_side_effect_is_suppressed_before_recovery_then_stopped(settings_store):
    from pydantic import BaseModel

    from clickachu.llm.provider import ToolCall
    from clickachu.tools.base import Tier, Tool, ToolResult
    from tests.fakes import QueueProvider

    class Args(BaseModel):
        name: str

    class LaunchFixture(Tool):
        name = "open_app"
        description = "Fixture launch"
        tier = Tier.LOW
        calls = 0

        def run(self, args, ctx):
            self.calls += 1
            return ToolResult(ok=True, summary="Opened fixture app.")

    LaunchFixture.Args = Args
    tool = LaunchFixture()
    registry = ToolRegistry()
    registry.register(tool)
    provider = QueueProvider([[ToolCall("launch", "open_app", '{"name":"fixture"}'), Done()]])
    events = []

    async def emit(event):
        events.append(event)

    agent = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=provider.factory,
        registry=registry,
    )
    await agent.handle_run(RunMessage(run_id="repeat", text="open fixture", mode="agent"))
    assert tool.calls == 1
    assert len(provider.calls) == 3
    assert "Duplicate action was not executed" in str(provider.calls[-1]["messages"])
    assert any(e.type == "run.error" and "repeated action loop" in e.message for e in events)
