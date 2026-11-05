from types import SimpleNamespace

import pytest

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import ApproveMessage, RunMessage
from clickachu.tools.base import ToolContext
from clickachu.tools.media import ControlMediaTool, MediaArgs, simple_media_request
from clickachu.tools.registry import ToolRegistry
from tests.fakes import FakeProvider, FakeSecretStore


class Session:
    source_app_user_model_id = "Spotify.exe"

    def __init__(self, status="paused", accepted=True):
        self.status, self.accepted, self.calls = status, accepted, []

    def get_playback_info(self):
        return SimpleNamespace(playback_status=SimpleNamespace(name=self.status.upper()))

    async def try_play_async(self):
        self.calls.append("play")
        if self.accepted:
            self.status = "playing"
        return self.accepted

    async def try_pause_async(self):
        self.calls.append("pause")
        self.status = "paused"
        return True

    async def try_get_media_properties_async(self):
        return SimpleNamespace(title="Example track", artist="Example artist")


@pytest.fixture
def media(monkeypatch):
    sessions = []

    async def manager():
        return SimpleNamespace(get_sessions=lambda: sessions)

    monkeypatch.setattr("clickachu.tools.media.media_manager", manager)
    return sessions


@pytest.mark.parametrize(
    "text",
    [
        "can you open spotify and play music",
        "play music on Spotify",
        "resume Spotify",
        "please pause Spotify",
        "open VLC and play music",
        "play music in Example Player",
        "pause MusicBee",
    ],
)
def test_generic_playback_routes_directly(text):
    assert simple_media_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "play Pungi on Spotify",
        "play my workout playlist on Spotify",
        "how do I play music on Spotify?",
        "don't play Spotify",
        "open Spotify and play music then send a message",
    ],
)
def test_specific_or_non_action_requests_stay_with_agent(text):
    assert simple_media_request(text) is None


@pytest.mark.parametrize(
    "initial,action,expected,calls",
    [
        ("paused", "play", "playing", ["play"]),
        ("playing", "play", "playing", []),
        ("playing", "pause", "paused", ["pause"]),
    ],
)
async def test_playback_is_idempotent_and_reads_back_state(
    db, media, initial, action, expected, calls
):
    session = Session(initial)
    media.append(session)
    ctx = ToolContext(run_id="r", step_id="s", snapshot=None, db=db)
    args = MediaArgs(app="Spotify", action=action)
    result = await ControlMediaTool().run(args, ctx)
    assert result.ok and result.data["status"] == expected
    assert session.calls == calls
    assert ControlMediaTool().verify(args, result)
    assert "Example track" in result.summary


async def test_rejected_playback_is_not_reported_as_success(db, media):
    media.append(Session(accepted=False))
    ctx = ToolContext(run_id="r", step_id="s", snapshot=None, db=db)
    result = await ControlMediaTool().run(MediaArgs(app="Spotify", action="play"), ctx)
    assert not result.ok and "did not confirm" in result.summary


@pytest.mark.parametrize("app", ["VLC", "ExamplePlayer"])
async def test_failed_direct_method_recovers_through_general_agent(settings_store, media, app):
    from clickachu.llm.provider import Done, TextDelta, ToolCall
    from clickachu.tools.base import Tier, Tool, ToolResult
    from clickachu.tools.computer import DesktopActionArgs, ObserveDesktopArgs
    from tests.fakes import QueueProvider

    session = Session(accepted=False)
    session.source_app_user_model_id = app + ".exe"
    media.append(session)
    acted = []

    class Observe(Tool):
        name, description, Args, tier = (
            "observe_desktop",
            "Inspect UI",
            ObserveDesktopArgs,
            Tier.READ,
        )

        def run(self, args, ctx):
            acted.append("observe")
            return ToolResult(ok=True, untrusted_text="observation_id=fresh\nc1 [Button] Play")

    class Click(Tool):
        name, description, Args, tier = "desktop_action", "Click", DesktopActionArgs, Tier.LOW

        def run(self, args, ctx):
            acted.append("click")
            session.status = "playing"
            return ToolResult(ok=True, summary="Player is now playing.")

    registry = ToolRegistry()
    for tool in (ControlMediaTool(), Observe(), Click()):
        registry.register(tool)
    provider = QueueProvider(
        [
            [ToolCall("observe", "observe_desktop", '{"title":"' + app + '"}'), Done()],
            [
                ToolCall(
                    "click",
                    "desktop_action",
                    '{"observation_id":"fresh","action":"click","ref":"c1"}',
                ),
                Done(),
            ],
            [TextDelta("Playing."), Done()],
        ]
    )
    events = []

    async def emit(event):
        events.append(event)
        if event.type == "approval.request":
            import asyncio

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
    await runner.handle_run(RunMessage(run_id="recover", text=f"play music on {app}"))
    assert acted == ["observe", "click"] and session.calls == ["play"]
    assert sum(e.type == "run.started" for e in events) == 1
    final = next(e for e in events if e.type == "run.finished")
    assert final.ok and final.metrics.actions == 5
    assert final.metrics.model_calls == 2
    assert "did not confirm" in str(provider.calls[0]["messages"])


async def test_no_session_never_plays_another_app(db, media):
    unrelated = Session()
    unrelated.source_app_user_model_id = "OtherPlayer.exe"
    media.append(unrelated)
    ctx = ToolContext(run_id="r", step_id="s", snapshot=None, db=db)
    result = await ControlMediaTool().run(MediaArgs(app="Spotify", action="play"), ctx)
    assert not result.ok and "no available media session" in result.summary
    assert not unrelated.calls


async def test_spotify_request_finishes_without_any_model_call(settings_store, media):
    media.append(Session())
    registry = ToolRegistry()
    registry.register(ControlMediaTool())
    provider = FakeProvider([])
    events = []

    async def emit(event):
        events.append(event)

    runner = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(key=None),
        registry=registry,
        provider_factory=provider.factory,
    )
    await runner.handle_run(RunMessage(run_id="media", text="can you open spotify and play music"))
    final = next(e for e in events if e.type == "run.finished")
    assert final.ok and "spotify: playing" in final.text
    assert final.metrics.model_calls == 0 and not provider.calls
    assert media[0].calls == ["play"]
