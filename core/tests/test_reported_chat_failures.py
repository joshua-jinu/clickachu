"""Regressions for failures seen in a real chat session (screen questions, Notepad, media...)."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import RunMessage
from clickachu.llm.documents import writing_brief
from clickachu.llm.provider import Done, TextDelta, ToolCall
from clickachu.llm.request_router import route_request
from clickachu.llm.router import heuristic_mode, notepad_write_requested
from clickachu.llm.synthesis import synthesize
from clickachu.tools.base import Tier, Tool, ToolResult
from clickachu.tools.media import MediaArgs
from clickachu.tools.registry import ToolRegistry
from tests.fakes import FakeProvider, FakeSecretStore, QueueProvider


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    import clickachu.llm.request_router as module

    monkeypatch.setattr(module, "_CACHE", {})
    monkeypatch.setattr(module, "today", lambda: date(2026, 11, 4))


@pytest.mark.parametrize(
    "text",
    [
        "In the notepad window add a recipe for a chocolate chip cookie",
        "add a poem to notepad",
        "open notepad and write a recipe for a chocolate chip cookie",
    ],
)
def test_adding_content_to_notepad_uses_the_document_workflow(text):
    assert notepad_write_requested(text)
    assert writing_brief(text).endswith(("chocolate chip cookie", "a poem"))
    assert "notepad" not in writing_brief(text).casefold()


@pytest.mark.parametrize("text", ["add notepad to the taskbar", "open notepad"])
def test_notepad_app_management_is_not_document_writing(text):
    assert not notepad_write_requested(text)


@pytest.mark.parametrize(
    "question",
    [
        "what is visible currently on my screen?",
        "can you see whats visible on my screen",
        "what windows are open",
    ],
)
def test_screen_questions_observe_the_desktop_instead_of_searching(question):
    provider = FakeProvider([])
    assert heuristic_mode(question) == "agent"
    result = asyncio.run(route_request(question, provider=provider, model="local"))
    assert result.route == "agent"
    assert not provider.calls


@pytest.mark.parametrize(
    "question",
    [
        "In which OTT platform can I watch sterling point?",
        "where can I watch sterling point online?",
    ],
)
def test_streaming_availability_is_researched_even_if_model_would_answer(question):
    provider = FakeProvider([TextDelta('{"route":"ask"}'), Done()])
    result = asyncio.run(route_request(question, provider=provider, model="local"))
    assert result.route == "research"
    assert not provider.calls


async def test_one_derived_number_does_not_discard_a_grounded_answer():
    provider = FakeProvider(
        [
            TextDelta(
                '{"answer":"Dharmendra died on 24 November 2025. He was 89 years old.",'
                '"source_ids":[1]}'
            ),
            Done(),
        ]
    )
    sources = [
        {
            "url": "https://example.com/a",
            "title": "Dharmendra",
            "text": "Dharmendra (8 December 1935 - 24 November 2025) was an Indian actor.",
        }
    ]
    events = [e async for e in synthesize(provider, "local", "who died recently?", sources)]
    answer = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "24 November 2025" in answer and "89" not in answer


def test_cancelled_request_is_not_replayed_as_history(settings_store):
    runner = Orchestrator(
        emit=lambda event: None,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=lambda *_: None,
    )
    runner._start_conversation("c", None, "open notepad and write a recipe")
    runner._start_conversation("c", None, "what time is it")
    runner._record_message("c", "assistant", "It is noon.")
    runner._start_conversation("c", None, "Open Chrome and open YouTube")
    assert runner._load_history("c") == [
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "It is noon."},
    ]


async def test_identical_failed_action_is_not_retried(settings_store):
    calls = []

    class Flaky(Tool):
        name, description, Args, tier = "control_media", "Media", MediaArgs, Tier.LOW

        def run(self, args, ctx):
            calls.append(args.app)
            return ToolResult(ok=False, summary="Spotify did not confirm playing playback.")

    registry = ToolRegistry()
    registry.register(Flaky())
    same = '{"app":"Spotify","action":"play","launch":true}'
    provider = QueueProvider(
        [
            [ToolCall("a", "control_media", same), Done()],
            [ToolCall("b", "control_media", same), Done()],
            [TextDelta("Could not start playback."), Done()],
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
        approval_timeout_s=1,
    )
    await runner.handle_run(RunMessage(run_id="r", text="use spotify somehow", mode="agent"))
    assert calls == ["Spotify"]
    assert "this exact call already failed" in str(provider.calls[2]["messages"])


async def test_freshly_launched_player_gets_play_reissued(db, monkeypatch):
    from clickachu.tools.base import ToolContext
    from clickachu.tools.media import ControlMediaTool

    class Session:
        source_app_user_model_id = "Spotify.exe"

        def __init__(self):
            self.calls = []
            self.status = "paused"

        def get_playback_info(self):
            return SimpleNamespace(playback_status=SimpleNamespace(name=self.status.upper()))

        async def try_play_async(self):
            self.calls.append("play")
            # The first command arrives before the app is ready and is ignored.
            if len(self.calls) > 1:
                self.status = "playing"
            return True

        async def try_get_media_properties_async(self):
            return None

    session = Session()
    sessions = []

    async def manager():
        return SimpleNamespace(get_sessions=lambda: sessions)

    async def run(worker, fn, args, ctx, timeout):
        sessions.append(session)
        return ToolResult(ok=True, summary="opened")

    monkeypatch.setattr("clickachu.tools.media.media_manager", manager)
    ctx = ToolContext(
        run_id="r", step_id="s", snapshot=None, db=db, workers=SimpleNamespace(run=run)
    )
    result = await ControlMediaTool().run(MediaArgs(app="Spotify", action="play", launch=True), ctx)
    assert result.ok and result.data["status"] == "playing"
    assert session.calls == ["play", "play"]


async def test_screen_question_only_offers_read_only_observation(settings_store):
    from clickachu.tools.computer import ObserveDesktopArgs
    from clickachu.tools.desktop import GetActiveContextArgs, ListWindowsArgs

    def tool(name, args, tier, text):
        class Fake(Tool):
            def run(self, args, ctx):
                return ToolResult(ok=True, summary=text, untrusted_text=text)

        Fake.name, Fake.description, Fake.Args, Fake.tier = name, name, args, tier
        return Fake()

    registry = ToolRegistry()
    for fake in (
        tool("get_active_context", GetActiveContextArgs, Tier.READ, "Active window: 'Report'"),
        tool("list_windows", ListWindowsArgs, Tier.READ, "- Report (Editor.exe)"),
        tool("observe_desktop", ObserveDesktopArgs, Tier.READ, "c1 [Document] Report text"),
        tool("web_search", ListWindowsArgs, Tier.READ, "should never run"),
    ):
        registry.register(fake)
    provider = QueueProvider(
        [
            [ToolCall("a", "get_active_context", "{}"), Done()],
            [TextDelta("Your screen shows the Report document in Editor."), Done()],
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
    await runner.handle_run(RunMessage(run_id="s", text="what is visible currently on my screen?"))
    offered = {schema["function"]["name"] for schema in provider.calls[0]["tools"]}
    assert offered == {"get_active_context", "list_windows", "observe_desktop"}
    final = next(e for e in events if e.type == "run.finished")
    assert final.ok and "Report document" in final.text
