"""Regression coverage for timeout reporting and partial actions after an LLM failure."""

import asyncio

import httpx
import openai
from pydantic import BaseModel

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import RunMessage
from clickachu.llm.openrouter import OpenRouterProvider
from clickachu.llm.provider import Done, LLMError, TextDelta, ToolCall
from clickachu.tools.base import Tier, Tool, ToolResult
from clickachu.tools.registry import ToolRegistry
from tests.fakes import FakeSecretStore


def test_local_timeout_is_not_reported_as_server_unreachable():
    provider = OpenRouterProvider(base_url="http://127.0.0.1:11434/v1", api_key="ollama")
    error = openai.APITimeoutError(request=httpx.Request("POST", "http://localhost/v1"))
    message = provider._format_api_error(error, mid_stream=True)
    assert "too long" in message
    assert "could not reach" not in message
    assert "does not mean it crashed" in message


def test_lost_stream_does_not_claim_ollama_is_stopped():
    provider = OpenRouterProvider(base_url="http://127.0.0.1:11434/v1", api_key="ollama")
    error = openai.APIConnectionError(request=httpx.Request("POST", "http://localhost/v1"))
    message = provider._format_api_error(error, mid_stream=True)
    assert "connection to Ollama was lost" in message
    assert "not been undone" in message
    assert "make sure Ollama is running" not in message


def test_followup_keeps_tool_evidence_after_model_failure(settings_store):
    class EmptyArgs(BaseModel):
        pass

    class InspectTool(Tool):
        name = "inspect_test"
        description = "Inspect a fake window"
        Args = EmptyArgs
        tier = Tier.READ

        def run(self, args, ctx):
            return ToolResult(ok=True, summary="Notepad window was observed")

        def verify(self, args, result):
            return True

    class Provider:
        def __init__(self):
            self.calls = []

        async def stream(self, model, messages, tools=None, max_tokens=2048):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                yield ToolCall("inspect_1", "inspect_test", "{}")
                yield Done()
            elif len(self.calls) == 2:
                raise LLMError("Model stream interrupted")
            else:
                yield TextDelta("The window was observed before the interruption.")
                yield Done()

    events = []

    async def emit(event):
        events.append(event)

    provider = Provider()
    registry = ToolRegistry()
    registry.register(InspectTool())
    orchestrator = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=lambda settings, key: provider,
        registry=registry,
    )

    async def scenario():
        await orchestrator.handle_run(
            RunMessage(
                run_id="r1",
                conversation_id="conversation",
                text="inspect",
                mode="agent",
            )
        )
        await orchestrator.handle_run(
            RunMessage(
                run_id="r2",
                conversation_id="conversation",
                text="What happened?",
                mode="ask",
            )
        )

    asyncio.run(scenario())
    history = "\n".join(m["content"] or "" for m in provider.calls[-1])
    assert "Notepad window was observed" in history
    assert "verified=True" in history
    assert '<untrusted_data source="action_result">' in history
    assert "Run interrupted before completion" in history
    assert any(e.type == "run.error" for e in events)
