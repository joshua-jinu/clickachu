import asyncio

import pytest

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import ApproveMessage, RunMessage, SettingsUpdate
from clickachu.llm.provider import Done, TextDelta
from clickachu.tools.base import Tier, Tool, ToolResult
from clickachu.tools.notepad import WriteNotepadArgs
from clickachu.tools.registry import ToolRegistry
from clickachu.tools.web_tools import WebFetchArgs, WebSearchArgs
from tests.fakes import FakeSecretStore, QueueProvider


@pytest.mark.parametrize("available", [True, False])
async def test_factual_writing_researches_before_composing_and_never_invents_nonexistence(
    settings_store, available
):
    settings_store.update(SettingsUpdate(provider="ollama"))
    order, drafts, events = [], [], []

    class Search(Tool):
        name, description, Args, tier = "web_search", "Search", WebSearchArgs, Tier.READ

        def run(self, args, ctx):
            order.append("search")
            assert args.query == "Example Academy"
            return ToolResult(
                ok=available,
                summary="Retrieved" if available else "Search unavailable",
                data={
                    "results": [{"url": "https://example.com/academy", "title": "Example Academy"}]
                },
            )

    class Fetch(Tool):
        name, description, Args, tier = "web_fetch", "Read", WebFetchArgs, Tier.READ

        def run(self, args, ctx):
            order.append("read")
            return ToolResult(ok=True, untrusted_text="Example Academy teaches robotics in Pune.")

    class Write(Tool):
        name, description, Args, tier = "write_notepad", "Write", WriteNotepadArgs, Tier.CONFIRM

        def run(self, args, ctx):
            order.append("write")
            drafts.append(args.text)
            return ToolResult(ok=True, summary="Wrote and verified the document.")

        def verify(self, args, result):
            return True

    registry = ToolRegistry()
    for tool in (Search(), Fetch(), Write()):
        registry.register(tool)
    provider = QueueProvider(
        [
            [
                TextDelta(
                    '{"answer":"Example Academy teaches robotics in Pune.","source_ids":[1]}'
                ),
                Done(),
            ]
        ]
    )

    async def emit(event):
        events.append(event)
        if event.type == "approval.request":
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.create_task(
                    runner.handle_approve(
                        ApproveMessage(
                            run_id=event.run_id, step_id=event.step_id, decision="approve"
                        )
                    )
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
    await runner.handle_run(
        RunMessage(run_id="factual", text="write a description about Example Academy in notepad")
    )
    if available:
        assert order == ["search", "read", "write"]
        assert drafts == ["Example Academy teaches robotics in Pune."]
        assert len(provider.calls) == 1
        assert next(e for e in events if e.type == "run.finished").ok
    else:
        assert order == ["search"] and not drafts and not provider.calls
        assert any(e.type == "run.error" for e in events)
