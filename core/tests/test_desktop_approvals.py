import time

import pytest
from pydantic import BaseModel

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import ApproveMessage, RunMessage
from clickachu.llm.provider import Done, TextDelta, ToolCall
from clickachu.tools import computer
from clickachu.tools.base import Tier, Tool, ToolResult
from clickachu.tools.computer import DesktopActionArgs, DesktopActionTool, Observation
from clickachu.tools.policy import PolicyAction, decide
from clickachu.tools.registry import ToolRegistry
from tests.fakes import FakeSecretStore, QueueProvider


@pytest.fixture
def observation(monkeypatch):
    obs = Observation(
        run_id="task",
        hwnd=100,
        pid=200,
        rectangle=(0, 0, 800, 600),
        captured=time.monotonic(),
        title="Example Player",
    )
    monkeypatch.setattr(computer, "_observations", {"fresh": obs})
    return obs


@pytest.mark.parametrize(
    "action,label,kind,extra",
    [
        ("click", "Play Riptide", "Button", {}),
        ("click", "Go back", "Button", {}),
        ("click", "Home", "Button", {}),
        ("click", "Search", "Button", {}),
        ("click", "Settings", "Button", {}),
        ("click", "More options", "Button", {}),
        ("click", "8", "Button", {}),
        ("click", "Riptide", "DataItem", {}),
        ("double_click", "Results", "TabItem", {}),
        ("fill", "What do you want to play?", "ComboBox", {"text": "Riptide", "submit": True}),
        ("fill", "Search", "Edit", {"text": "Microsoft news", "submit": True}),
        ("keys", "Search", "Edit", {"keys": "{ENTER}"}),
        ("keys", "", "", {"keys": "^f"}),
        ("scroll", "", "", {"wheel": -2}),
    ],
)
def test_routine_desktop_actions_need_no_approval(observation, action, label, kind, extra):
    observation.controls["c1"] = ((1,), "id", kind, label)
    args = DesktopActionArgs(observation_id="fresh", action=action, ref="c1", **extra)
    assert decide(DesktopActionTool(), args, run_id="task").action == PolicyAction.AUTO


@pytest.mark.parametrize(
    "label,kind",
    [
        ("Buy now", "Button"),
        ("Send message", "Button"),
        ("Delete playlist", "MenuItem"),
        ("Install", "Button"),
        ("Save changes", "Button"),
        ("Allow access", "Button"),
        ("Confirm", "Button"),
        ("OK", "Button"),
        ("Play and purchase", "Button"),
        ("setup.exe", "ListItem"),
        ("Unknown control", "Button"),
    ],
)
def test_consequential_or_ambiguous_actions_still_ask(observation, label, kind):
    observation.controls["c1"] = ((1,), "id", kind, label)
    args = DesktopActionArgs(observation_id="fresh", action="click", ref="c1")
    decision = decide(DesktopActionTool(), args, run_id="task", trusted={("desktop_action", "*")})
    assert decision.action == PolicyAction.ASK


def test_stale_foreign_sensitive_and_unidentified_targets_do_not_auto_approve(observation):
    observation.controls["c1"] = ((1,), "id", "Button", "Play")
    args = DesktopActionArgs(observation_id="fresh", action="click", ref="c1")
    assert decide(DesktopActionTool(), args, run_id="other-task").action == PolicyAction.ASK
    observation.captured -= 301
    assert decide(DesktopActionTool(), args, run_id="task").action == PolicyAction.ASK

    observation.captured = time.monotonic()
    observation.title = "paypal.com - Payment"
    assert decide(DesktopActionTool(), args, run_id="task").action == PolicyAction.ASK
    observation.title = "Example Player"
    args = DesktopActionArgs(observation_id="fresh", action="click", x=20, y=20)
    assert decide(DesktopActionTool(), args, run_id="task").action == PolicyAction.ASK


@pytest.mark.parametrize("text", ["javascript:alert(1)", "file:///C:/secret", "shell:startup"])
def test_address_entry_does_not_auto_execute_active_content(observation, text):
    observation.controls["c1"] = ((1,), "id", "Edit", "Address and search bar")
    args = DesktopActionArgs(
        observation_id="fresh", action="fill", ref="c1", text=text, submit=True
    )
    assert decide(DesktopActionTool(), args, run_id="task").action == PolicyAction.ASK


async def test_immediate_approval_is_registered_before_the_card_is_broadcast(settings_store):
    calls = []

    class ImportantArgs(BaseModel):
        pass

    class ImportantAction(Tool):
        name = "important_action"
        description = "Fixture requiring explicit approval"
        Args = ImportantArgs
        tier = Tier.CONFIRM

        def run(self, args, ctx):
            calls.append("executed")
            return ToolResult(ok=True, summary="Fixture completed")

    registry = ToolRegistry()
    registry.register(ImportantAction())
    provider = QueueProvider(
        [
            [ToolCall("first", "important_action", "{}"), Done()],
            [TextDelta("Finished."), Done()],
        ]
    )
    events = []

    async def emit(event):
        events.append(event)
        if event.type == "approval.request":
            # Reply inside the send itself, before _await_approval can run.
            await runner.handle_approve(
                ApproveMessage(run_id=event.run_id, step_id=event.step_id, decision="approve")
            )

    runner = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        registry=registry,
        provider_factory=provider.factory,
        approval_timeout_s=0.05,
    )
    await runner.handle_run(RunMessage(run_id="fast-approval", text="Do the task", mode="agent"))
    assert calls == ["executed"]
    assert not any(e.type == "step.finished" and "timed out" in e.summary for e in events)
