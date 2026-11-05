import asyncio
from datetime import date

import pytest

from clickachu.llm.prompts import system_base_prompt
from clickachu.llm.provider import Done, TextDelta
from clickachu.llm.request_router import route_request
from tests.fakes import FakeProvider, Hang


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    import clickachu.llm.request_router as module

    monkeypatch.setattr(module, "_CACHE", {})
    monkeypatch.setattr(module, "today", lambda: date(2026, 11, 4))


@pytest.mark.parametrize(
    "question",
    [
        "Help me understand how tidal energy works",
        "Is Acme Academy a university or a training provider?",
        "I'd like some background on that organization",
        "Compare the cost of these laptops",
    ],
)
def test_semantic_research_is_not_limited_to_topic_or_sentence_shape(question):
    provider = FakeProvider([TextDelta('{"route":"research","query":"resolved topic"}'), Done()])
    result = asyncio.run(route_request(question, provider=provider, model="local"))
    assert result.route == "research"
    assert result.query == "resolved topic"
    assert len(provider.calls) == 1
    assert "ANY wording" in provider.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "question",
    [
        "which Indian actor died recently.",
        "What happened in the election yesterday?",
        "Give me the latest space news",
    ],
)
def test_recent_questions_search_even_if_model_would_choose_ask(question):
    provider = FakeProvider([TextDelta('{"route":"ask"}'), Done()])
    result = asyncio.run(route_request(question, provider=provider, model="local"))
    assert result.route == "research"
    assert result.recent
    assert "November 2026" not in result.query
    assert not provider.calls


@pytest.mark.parametrize("question", ["hello", "who are you?", "2+2", "open notepad"])
def test_obvious_requests_need_no_classifier(question):
    provider = FakeProvider([])
    result = asyncio.run(route_request(question, provider=provider, model="local"))
    assert result.route == ("agent" if question == "open notepad" else "ask")
    assert not provider.calls


@pytest.mark.parametrize(
    "question",
    [
        "Write a poem about rain",
        "Summarize the paragraph I supplied",
        "Translate this into Hindi",
    ],
)
def test_creative_and_supplied_content_can_stay_local(question):
    provider = FakeProvider([TextDelta('{"route":"ask"}'), Done()])
    assert asyncio.run(route_request(question, provider=provider, model="local")).route == "ask"


def test_no_search_request_is_respected():
    provider = FakeProvider([])
    result = asyncio.run(
        route_request(
            "Don't search online; explain the latest paragraph I gave you",
            provider=provider,
            model="local",
        )
    )
    assert result.route == "ask"
    assert not provider.calls


@pytest.mark.parametrize("script", [[TextDelta("not JSON"), Done()], [Hang()]])
def test_broken_classifier_does_not_fall_back_to_stale_factual_answer(script):
    provider = FakeProvider(script)
    result = asyncio.run(
        route_request(
            "Explain Acme's business",
            provider=provider,
            model="local",
            timeout_s=0.02,
        )
    )
    assert result.route == "research"


def test_general_answer_prompt_has_runtime_date(monkeypatch):
    import clickachu.llm.time_context as module

    monkeypatch.setattr(module, "today", lambda: date(2030, 2, 3))
    prompt = system_base_prompt("local-model")
    assert "2030-02-03" in prompt
    assert "training cutoff is not today's date" in prompt


def test_classifier_cannot_invent_a_stale_year_for_current_question():
    provider = FakeProvider(
        [
            TextDelta('{"route":"research","query":"actor death 2024","recent":true}'),
            Done(),
        ]
    )
    decision = asyncio.run(
        route_request("Any updates on that actor?", provider=provider, model="local")
    )
    assert "2024" not in decision.query
    assert "November 2026" not in decision.query
