import asyncio
import json

import pytest

from clickachu.agent.goals import request_goal
from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import ApproveMessage, RunMessage
from clickachu.llm.provider import Done, ToolCall
from clickachu.tools.base import Tier, Tool, ToolResult
from clickachu.tools.computer import DesktopActionArgs
from clickachu.tools.registry import ToolRegistry
from tests.fakes import FakeProvider, FakeSecretStore


@pytest.mark.parametrize(
    "text,app,label",
    [
        ("Open Calculator and click the 8 button", "Calculator", "8"),
        ("click Continue in Example App", "Example App", "Continue"),
        ("press the Confirm button in Editor", "Editor", "Confirm"),
    ],
)
def test_literal_single_click_contract_is_app_independent(text, app, label):
    goal = request_goal(text)
    assert goal.kind == "ui_click" and goal.target == app and goal.control == label


@pytest.mark.parametrize(
    "text",
    [
        "click Eight twice in Calculator",
        "click Eight then Nine in Calculator",
        "don't click Eight in Calculator",
        "how do I click Eight in Calculator?",
    ],
)
def test_multi_click_and_non_action_requests_do_not_end_after_one_click(text):
    assert request_goal(text) is None


async def test_confirmed_requested_click_ends_without_an_extra_model_call(settings_store):
    class Click(Tool):
        name = "desktop_action"
        description = "Click fixture"
        Args = DesktopActionArgs
        tier = Tier.LOW

        def run(self, args, ctx):
            return ToolResult(
                ok=True,
                summary="Click dispatched",
                data={
                    "performed_action": {
                        "action": "click",
                        "target": "Eight",
                        "window": "Calculator",
                        "process": "CalculatorApp.exe",
                        "input_dispatched": True,
                    }
                },
            )

    provider = FakeProvider(
        [
            ToolCall(
                "click",
                "desktop_action",
                json.dumps({"observation_id": "fixture", "action": "click", "ref": "c1"}),
            ),
            Done(),
        ]
    )
    registry = ToolRegistry()
    registry.register(Click())
    events = []

    async def emit(event):
        events.append(event)
        if event.type == "approval.request":
            asyncio.create_task(
                runner.handle_approve(
                    ApproveMessage(run_id=event.run_id, step_id=event.step_id, decision="approve")
                )
            )

    runner = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        registry=registry,
        provider_factory=provider.factory,
        approval_timeout_s=1,
    )
    await runner.handle_run(RunMessage(run_id="click", text="Click the 8 button in Calculator"))
    finished = next(e for e in events if e.type == "run.finished")
    assert finished.ok and finished.metrics.model_calls == 1 and len(provider.calls) == 1
    assert finished.text == "Clicked 'Eight' in Calculator."


def test_unrelated_or_unconfirmed_click_cannot_complete_the_requested_goal():
    goal = request_goal("Click Continue in Example App")
    args = DesktopActionArgs(observation_id="fixture", action="click", ref="c1")
    for changed in (
        {"target": "Delete"},
        {"window": "Other app", "process": "Other.exe"},
        {"input_dispatched": False},
    ):
        performed = {
            "action": "click",
            "target": "Continue",
            "window": "Example App",
            "process": "Example.exe",
            "input_dispatched": True,
            **changed,
        }
        assert not goal.completed(
            "desktop_action", args, ToolResult(ok=True, data={"performed_action": performed}), None
        )
