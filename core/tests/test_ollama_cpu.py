import json

import httpx
import pytest

from clickachu.llm import ollama_cpu
from clickachu.llm.openrouter import OpenRouterProvider
from clickachu.llm.provider import Done, LLMError, TextDelta, ToolCall
from clickachu.tools.registry import create_default_registry


@pytest.fixture
def transport(monkeypatch):
    calls = []
    original = httpx.AsyncClient

    def install(chunks, status=200):
        def handle(request):
            calls.append(request)
            return httpx.Response(status, text="\n".join(json.dumps(c) for c in chunks))

        monkeypatch.setattr(
            ollama_cpu.httpx,
            "AsyncClient",
            lambda **kw: original(transport=httpx.MockTransport(handle), **kw),
        )

    return install, calls


async def test_cpu_mode_sends_explicit_runtime_options_and_streams_usage(transport):
    install, calls = transport
    install(
        [
            {"message": {"content": "hello"}},
            {"done": True, "prompt_eval_count": 12, "eval_count": 3},
        ]
    )
    provider = OpenRouterProvider(
        base_url="http://localhost:11434/v1/", api_key="", ollama_cpu_only=True
    )
    events = [
        e
        async for e in provider.stream(
            "qwen3:4b-instruct", [{"role": "user", "content": "hello"}], max_tokens=50
        )
    ]
    assert calls[0].url.path == "/api/chat"
    body = json.loads(calls[0].content)
    assert body["options"] == {"num_gpu": 0, "num_ctx": 4096, "num_predict": 50}
    assert body["think"] is False
    assert isinstance(events[0], TextDelta) and events[0].text == "hello"
    assert events[-1].usage.input_tokens == 12 and events[-1].usage.output_tokens == 3
    assert provider._client.max_retries == 0


async def test_cpu_actions_use_same_validation_and_schema(transport):
    install, calls = transport
    install(
        [
            {"message": {"content": '{"tool":"open_app","arguments":{"name":"Spotify"}}'}},
            {"done": True},
        ]
    )
    provider = OpenRouterProvider(
        base_url="http://localhost:11434/v1", api_key="", ollama_cpu_only=True
    )
    events = [
        e
        async for e in provider.stream_agent(
            "fixture", [], create_default_registry().schemas(names=["open_app"]), require_tool=True
        )
    ]
    assert isinstance(events[0], ToolCall) and events[0].name == "open_app"
    assert isinstance(events[-1], Done)
    assert "anyOf" in json.loads(calls[0].content)["format"]


async def test_structured_actions_disable_reasoning_even_when_chat_reasoning_is_enabled(transport):
    install, calls = transport
    install(
        [
            {"message": {"content": '{"tool":"open_app","arguments":{"name":"Example"}}'}},
            {"done": True},
        ]
    )
    provider = OpenRouterProvider(
        base_url="http://localhost:11434/v1",
        api_key="",
        ollama_cpu_only=True,
        reasoning_effort="high",
    )
    _ = [
        e
        async for e in provider.stream_agent(
            "fixture", [], create_default_registry().schemas(names=["open_app"]), require_tool=True
        )
    ]
    assert json.loads(calls[0].content)["think"] is False


async def test_runaway_structured_thinking_is_aborted_without_action(transport):
    install, _ = transport
    install([{"message": {"thinking": "普京" * 40}}, {"done": True}])
    provider = OpenRouterProvider(
        base_url="http://localhost:11434/v1", api_key="", ollama_cpu_only=True
    )
    events = []
    with pytest.raises(LLMError, match="drifted"):
        async for event in provider.stream_agent(
            "fixture", [], create_default_registry().schemas(names=["open_app"]), require_tool=True
        ):
            events.append(event)
    assert events == []


async def test_optional_local_summary_model_does_not_replace_desktop_model(transport):
    install, calls = transport
    install([{"message": {"content": '{"answer":"Evidence.","source_ids":[1]}'}}, {"done": True}])
    provider = OpenRouterProvider(
        base_url="http://localhost:11434/v1",
        api_key="",
        ollama_cpu_only=True,
        summary_model="small-local",
    )
    _ = [event async for event in provider.stream_summary("main-local", [], source_ids=[1])]
    summary = json.loads(calls[0].content)
    assert summary["model"] == "small-local" and summary["keep_alive"] == 0
    assert summary["options"]["temperature"] == 0
    _ = [event async for event in provider.stream("main-local", [])]
    assert json.loads(calls[1].content)["model"] == "main-local"


@pytest.mark.parametrize(
    "chunks,status",
    [
        ([{"error": "runner stopped"}], 200),
        ([{"message": {"content": "incomplete"}}], 200),
        ([], 500),
    ],
)
async def test_cpu_failure_never_executes_partial_actions(transport, chunks, status):
    install, _ = transport
    install(chunks, status)
    provider = OpenRouterProvider(
        base_url="http://localhost:11434/v1", api_key="", ollama_cpu_only=True
    )
    emitted = []
    with pytest.raises(LLMError):
        async for event in provider.stream_agent(
            "fixture", [], create_default_registry().schemas(names=["open_app"]), require_tool=True
        ):
            emitted.append(event)
    assert not any(isinstance(e, ToolCall) for e in emitted)
