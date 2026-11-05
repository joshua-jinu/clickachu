import asyncio

import pytest

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import RunMessage, SettingsUpdate
from clickachu.llm.documents import clean_document, writing_brief
from clickachu.llm.provider import Done, TextDelta
from clickachu.llm.router import lookup_query
from clickachu.tools.base import Tier, Tool, ToolResult
from clickachu.tools.notepad import _normalise
from clickachu.tools.registry import ToolRegistry
from clickachu.tools.web_tools import WebFetchArgs, WebSearchArgs, blocked_page
from tests.fakes import FakeSecretStore, QueueProvider


def test_writing_brief_separates_desktop_instructions():
    assert writing_brief("can you open notepad and write me a description about porsche?") == (
        "a description about porsche"
    )
    assert "notepad" not in writing_brief("write a poem in notepad").lower()


@pytest.mark.parametrize(
    "text",
    [
        "I can't open Notepad or interact with your file system. However, here is a paragraph.",
        "Porsche is a company. You can copy and paste this directly into Notepad.",
    ],
)
def test_disclaimers_never_become_document_content(text):
    with pytest.raises(ValueError):
        clean_document(text)


def test_notepad_verification_normalizes_uia_paragraph_breaks():
    assert _normalise("First\rSecond\r") == _normalise("First\nSecond\n")
    assert _normalise("First\u2029Second\r\n") == _normalise("First\nSecond")
    assert _normalise("First second") != _normalise("First\nSecond")


def test_automatic_fact_lookup_and_followup_topic():
    assert (
        lookup_query(
            "you can checkonline and tell me",
            [
                {"role": "user", "content": "what is Kalvium?"},
                {"role": "assistant", "content": "I am not familiar with it."},
            ],
        )
        == "Kalvium"
    )


def test_captcha_is_a_block_not_a_successful_search():
    assert blocked_page("https://www.google.com/sorry/index?continue=/search", "")
    assert blocked_page("https://example.com", "Verify you are human")
    assert not blocked_page("https://example.com/about", "Kalvium company information")


def test_search_parses_sources_and_caches_results(monkeypatch):
    import clickachu.tools.web_tools as module

    calls = []
    xml = (
        "<rss><channel><item><title>Official company</title>"
        "<link>https://example.com/about</link>"
        "<description>A unique test company description.</description></item></channel></rss>"
    )

    def download(url):
        calls.append(url)
        return url, xml

    monkeypatch.setattr(module, "_download", download)
    monkeypatch.setattr(module, "_CACHE", {})
    assert (
        module.search_web("unique test company", provider="bing")[0]["url"]
        == "https://example.com/about"
    )
    assert (
        module.search_web("unique test company", provider="bing")[0]["title"] == "Official company"
    )
    assert len(calls) == 1


def test_search_transport_failure_is_not_empty_results(monkeypatch):
    import clickachu.tools.web_tools as module

    def unavailable(query, **kwargs):
        raise OSError("Network unavailable")

    monkeypatch.setattr(module, "search_web", unavailable)
    tool = module.WebSearchTool()
    result = tool.run(WebSearchArgs(query="Kalvium"), None)
    assert not result.ok
    assert not tool.verify(None, result)
    assert "could not verify" in result.summary
    assert "does not mean" in result.summary


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("recent", [True, False])
def test_research_runs_before_answer_and_failures_are_honest(
    settings_store, available, monkeypatch, recent
):
    import clickachu.llm.request_router as router

    monkeypatch.setattr(router, "_CACHE", {})
    settings_store.update(
        SettingsUpdate(
            provider="ollama",
            ollama_model="qwen3:4b-instruct",
            auto_web_lookup=True,
        )
    )
    calls = []

    class FakeSearch(Tool):
        name = "web_search"
        description = "Background search"
        Args = WebSearchArgs
        tier = Tier.READ

        def run(self, args, ctx):
            calls.append(args)
            if not available:
                return ToolResult(
                    ok=False, summary="Search blocked; I could not verify this online."
                )
            return ToolResult(
                ok=True,
                data={
                    "results": [{"url": "https://kalvium.com/", "title": "Kalvium official source"}]
                },
                summary="Sources retrieved",
                untrusted_text=(
                    "Kalvium is a work-integrated upskilling program. Source: https://kalvium.com/"
                ),
            )

        def verify(self, args, result):
            return result.ok

    registry = ToolRegistry()
    registry.register(FakeSearch())

    class FakeFetch(Tool):
        name = "web_fetch"
        description = "Read a source"
        Args = WebFetchArgs
        tier = Tier.READ

        def run(self, args, ctx):
            return ToolResult(
                ok=True,
                untrusted_text=(
                    "Kalvium is a work-integrated upskilling program. Source: https://kalvium.com/"
                ),
            )

    registry.register(FakeFetch())
    provider = QueueProvider(
        [
            [
                TextDelta(
                    '{"action":"answer","answer":"Source-backed answer.","sources":["https://kalvium.com/"]}'
                    if available
                    else '{"action":"stop","answer":"I could not verify this online."}'
                ),
                Done(),
            ],
        ]
    )
    events = []

    async def emit(event):
        events.append(event)

    runner = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        registry=registry,
        provider_factory=provider.factory,
    )
    question = "latest news about Kalvium" if recent else "what is Kalvium?"
    asyncio.run(runner.handle_run(RunMessage(run_id="research", text=question)))
    assert len(calls) == 1
    assert calls[0].query == "Kalvium"
    assert len(provider.calls) == int(available)  # One synthesis; mechanical steps need no model.
    if available:
        evidence = str(provider.calls[-1]["messages"])
        assert "work-integrated upskilling" in evidence
        assert "Date:" in evidence
        assert "Publication dates are not event dates" in evidence
    if available:
        final = next(e for e in events if e.type == "run.finished")
        assert final.ok
    else:
        error = next(e for e in events if e.type == "run.error")
        assert "No answer was verified" in error.message
