import asyncio
import json
from types import SimpleNamespace

import pytest

from clickachu.llm.provider import Done, LLMError, TextDelta, ToolCall
from clickachu.llm.research import ResearchState
from clickachu.tools.base import ToolResult
from tests.fakes import QueueProvider


def scripted(*decisions):
    return QueueProvider([[TextDelta(json.dumps(d)), Done()] for d in decisions])


async def turn(state, provider, messages=None):
    return [e async for e in state.events(provider, "one-local-model", messages or [])]


@pytest.mark.parametrize(
    "objective",
    [
        "Compare two battery technologies",
        "Explain an unfamiliar organization",
        "Find a historical event's date",
        "Find current travel restrictions",
    ],
)
def test_controller_can_research_arbitrary_objectives(objective):
    async def run():
        state = ResearchState()
        provider = scripted(
            {"action": "search", "query": "first attempt", "provider": "bing"},
            {"action": "search", "query": "specific missing evidence", "provider": "wikipedia"},
            {"action": "read", "url": "https://example.com/report"},
            {
                "action": "answer",
                "answer": "Supported conclusion.",
                "sources": ["https://example.com/report"],
            },
        )
        messages = [{"role": "user", "content": objective}]
        first = (await turn(state, provider, messages))[-1]
        assert isinstance(first, ToolCall)
        state.observe("web_search", None, ToolResult(ok=False, summary="Irrelevant or blocked"))
        messages.append({"role": "tool", "content": "Irrelevant or blocked results"})
        second = (await turn(state, provider, messages))[-1]
        assert json.loads(second.arguments_json)["provider"] == "wikipedia"
        assert json.loads(second.arguments_json)["query"] == "specific missing evidence"
        state.observe(
            "web_search",
            None,
            ToolResult(
                ok=True,
                data={
                    "results": [
                        {
                            "url": "https://example.com/report",
                            "title": "Source report",
                        }
                    ]
                },
            ),
        )
        read = (await turn(state, provider, messages))[-1]
        assert read.name == "web_fetch"
        state.observe("web_fetch", None, ToolResult(ok=True, summary="Supporting page text"))
        answer = (await turn(state, provider, messages))[-1]
        assert isinstance(answer, TextDelta)
        assert "[Source](https://example.com/report)" in answer.text
        assert all(c["model"] == "one-local-model" for c in provider.calls)
        assert objective in provider.calls[-1]["messages"][1]["content"]
        assert "Irrelevant or blocked" in str(provider.calls[-1]["messages"])

    asyncio.run(run())


@pytest.mark.parametrize(
    "decision",
    [
        {"action": "answer", "answer": "Unsupported claim", "sources": ["https://invented.test"]},
        {"action": "read", "url": "https://invented.test"},
    ],
)
def test_unobserved_sources_cannot_be_read_or_cited(decision):
    with pytest.raises(LLMError):
        asyncio.run(turn(ResearchState(), scripted(decision)))


def test_repeated_queries_and_action_budget_are_enforced():
    async def run():
        decision = {"action": "search", "query": "unchanged", "provider": "bing"}
        state = ResearchState(max_actions=1)
        provider = scripted(decision, decision)
        await turn(state, provider)
        with pytest.raises(LLMError, match="budget"):
            await turn(state, provider)

    asyncio.run(run())


def test_failed_lookup_does_not_allow_unverified_answer():
    async def run():
        state = ResearchState()
        state.observe("web_search", None, ToolResult(ok=False, summary="Network failure"))
        with pytest.raises(LLMError, match="citations"):
            await turn(state, scripted({"action": "answer", "answer": "I know the fact"}))

    asyncio.run(run())


def test_malformed_decision_gets_one_repair_without_executing_it():
    async def run():
        provider = QueueProvider(
            [
                [TextDelta("incomplete JSON"), Done()],
                [TextDelta('{"action":"search","query":"model-selected terms"}'), Done()],
            ]
        )
        state = ResearchState()
        events = await turn(state, provider)
        assert len(provider.calls) == 2
        assert len(state.attempts) == 1
        assert json.loads(events[-1].arguments_json)["query"] == "model-selected terms"

    asyncio.run(run())


async def test_one_engine_rejects_irrelevant_candidates_reads_and_synthesizes_once():
    from clickachu.llm.research_subject import preserve_subject

    objective = "can you tell me about provident sunworth"
    assert (
        preserve_subject(objective, "Providence Sunworth company profile") == "provident sunworth"
    )
    state = ResearchState(objective=objective, initial_query="provident sunworth")
    provider = scripted(
        {
            "action": "answer",
            "answer": "A housing project.",
            "sources": ["https://example.com/provident-sunworth"],
        }
    )
    first = (await turn(state, provider))[-1]
    assert json.loads(first.arguments_json)["query"] == "provident sunworth"
    state.observe(
        "web_search",
        None,
        ToolResult(
            ok=True,
            data={
                "results": [
                    {
                        "url": "https://providence.example",
                        "title": "Providence healthcare",
                        "snippet": "Unrelated healthcare services",
                    },
                    {
                        "url": "https://example.com/provident-sunworth",
                        "title": "Provident Sunworth",
                        "snippet": "Housing project",
                    },
                ]
            },
        ),
    )
    assert json.loads(first.arguments_json)["provider"] == "duckduckgo"
    url = "https://example.com/provident-sunworth"
    read = (await turn(state, provider))[-1]
    assert read.name == "web_fetch" and json.loads(read.arguments_json)["url"] == url
    state.observe(
        "web_fetch",
        SimpleNamespace(url=url),
        ToolResult(
            ok=True, untrusted_text="URL: " + url + "\nProvident Sunworth is a housing project."
        ),
    )
    answer = (await turn(state, provider, [{"role": "tool", "content": "UNRELATED_RAW_RESULT"}]))[
        -1
    ]
    assert isinstance(answer, TextDelta) and url in answer.text
    assert len(provider.calls) == state.model_calls == 1
    prompt = str(provider.calls[0]["messages"])
    assert "UNRELATED_RAW_RESULT" not in prompt and "providence.example" not in prompt
    assert "housing project" in prompt


async def test_placeholder_stop_is_replaced_with_specific_failure():
    state = ResearchState(objective="tell me about Example Topic", initial_query="Example Topic")
    state.sources["https://example.com"] = "Example Topic"
    state.read_sources.add("https://example.com")
    state.attempts.add(("search", "example topic", "bing", None))
    with pytest.raises(LLMError, match="Example Topic") as error:
        await turn(state, scripted({"action": "stop", "answer": "what remains unverified"}))
    assert "what remains unverified" not in str(error.value)


def test_login_results_are_not_used_as_subject_evidence():
    state = ResearchState(objective="tell me about Example School")
    state.observe(
        "web_search",
        None,
        ToolResult(
            ok=True,
            data={
                "results": [
                    {"url": "https://example.com/login", "title": "Example School sign in"},
                    {"url": "https://example.com/about", "title": "About Example School"},
                ]
            },
        ),
    )
    assert list(state.sources) == ["https://example.com/about"]
