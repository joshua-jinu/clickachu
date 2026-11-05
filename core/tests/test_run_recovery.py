"""Agent setup crashes and cancellation must always release the renderer's busy state."""

import asyncio

import pytest

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import CancelMessage, RunMessage, SettingsUpdate
from clickachu.llm.provider import Done, LLMError, TextDelta
from clickachu.tools.base import Tier, Tool, ToolResult
from clickachu.tools.desktop import OpenAppArgs
from clickachu.tools.registry import ToolRegistry
from clickachu.tools.web_tools import WebSearchTool
from tests.fakes import FakeProvider, FakeSecretStore, Hang


def runner_for(settings_store, events, provider=None, registry=None):
    async def emit(event):
        events.append(event)

    provider = provider or FakeProvider([TextDelta("Hello"), Done()])
    return Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=provider.factory,
        registry=registry if registry is not None else ToolRegistry(),
    )


@pytest.mark.parametrize(
    "error", [RuntimeError("fixture"), LLMError("server unavailable"), TimeoutError()]
)
async def test_unhandled_setup_errors_end_the_run_and_allow_retry(
    settings_store, monkeypatch, error
):
    events = []
    runner = runner_for(settings_store, events)
    original = runner._run

    async def broken(*args):
        raise error

    monkeypatch.setattr(runner, "_run", broken)
    await runner.handle_run(RunMessage(run_id="broken", text="latest news"))
    assert len([e for e in events if e.type == "run.error"]) == 1
    assert events[-1].type == "orb.state" and events[-1].state == "error"
    assert not runner._runs
    monkeypatch.setattr(runner, "_run", original)
    await runner.handle_run(RunMessage(run_id="retry", text="hello", conversation_id="broken"))
    assert any(e.type == "run.finished" and e.run_id == "retry" and e.ok for e in events)


@pytest.mark.parametrize("stage", ["routing", "ask", "agent", "search"])
async def test_stop_during_routing_generation_or_search_emits_one_terminal_event(
    settings_store, monkeypatch, stage
):
    entered = asyncio.Event()

    class WaitingProvider(FakeProvider):
        async def stream(self, *args, **kwargs):
            entered.set()
            async for event in super().stream(*args, **kwargs):
                yield event

    provider = WaitingProvider([TextDelta("Partial answer"), Hang()])
    registry = ToolRegistry()
    if stage in ("routing", "search"):
        registry.register(WebSearchTool())
        settings_store.update(SettingsUpdate(auto_web_lookup=True))
    if stage == "routing":

        async def route(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr("clickachu.agent.orchestrator.route_request", route)
    if stage == "search":

        async def search(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(WebSearchTool, "run", search)
    events = []
    runner = runner_for(settings_store, events, provider, registry)
    text = "latest news about space" if stage == "search" else "hello"
    msg = RunMessage(run_id="stop", text=text, mode="agent" if stage == "agent" else None)
    task = asyncio.create_task(runner.handle_run(msg))
    await asyncio.wait_for(entered.wait(), timeout=2)
    await runner.handle_cancel(CancelMessage(run_id="stop"))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    terminal = [e for e in events if e.type in ("run.finished", "run.error")]
    assert len(terminal) == 1
    assert terminal[0].type == "run.finished" and not terminal[0].ok
    assert not runner._runs
    assert events[-1].type == "orb.state" and events[-1].state == "idle"


async def test_stop_acknowledges_a_stale_renderer_run(settings_store):
    events = []
    runner = runner_for(settings_store, events)
    await runner.handle_cancel(CancelMessage(run_id="old-crashed-run"))
    assert events[0].type == "run.finished"
    assert events[0].status == "cancelled"


async def test_compound_request_bootstraps_the_requested_microsoft_app(settings_store):
    settings_store.update(SettingsUpdate(agent_max_steps=1))
    opened = []

    class OpenApp(Tool):
        name = "open_app"
        description = "Open a fixture app"
        Args = OpenAppArgs
        tier = Tier.READ

        def run(self, args, ctx):
            opened.append(args.name)
            return ToolResult(ok=True, summary="Opened fixture app")

        def verify(self, args, result):
            return result.ok

    registry = ToolRegistry()
    registry.register(OpenApp())
    events = []
    runner = runner_for(settings_store, events, registry=registry)
    await runner.handle_run(
        RunMessage(
            run_id="compound", text="open Microsoft Edge and search for space news", mode="agent"
        )
    )
    assert opened == ["Microsoft Edge"]
    assert any(e.type == "step.finished" and e.ok for e in events)
    assert any(e.type in ("run.finished", "run.error") for e in events)
