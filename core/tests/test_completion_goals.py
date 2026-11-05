import pytest

from clickachu.agent.goals import request_goal, resume_requested
from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import RunMessage
from clickachu.tools.media import ControlMediaTool
from clickachu.tools.registry import ToolRegistry
from tests.fakes import FakeProvider, FakeSecretStore
from tests.test_media import Session


@pytest.mark.parametrize("app", ["Spotify", "VLC", "ExamplePlayer"])
async def test_play_pause_and_contextual_followup_finish_without_model_calls(
    settings_store, monkeypatch, app
):
    session = Session()
    session.source_app_user_model_id = app + ".exe"

    async def manager():
        from types import SimpleNamespace

        return SimpleNamespace(get_sessions=lambda: [session])

    monkeypatch.setattr("clickachu.tools.media.media_manager", manager)
    registry = ToolRegistry()
    registry.register(ControlMediaTool())
    provider = FakeProvider([])
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
    for i, text in enumerate(
        (
            f"open {app} and play music",
            f"stop the music playing in {app}",
            "play the music",
            "pause it",
        )
    ):
        await runner.handle_run(RunMessage(run_id=f"r{i}", conversation_id="same", text=text))
    finished = [e for e in events if e.type == "run.finished"]
    assert len(finished) == 4 and all(e.ok for e in finished)
    assert all(e.metrics.model_calls == 0 and e.metrics.actions == 1 for e in finished)
    assert session.calls == ["play", "pause", "play", "pause"]
    assert not provider.calls


@pytest.mark.parametrize(
    "text",
    [
        "play my favourite song",
        "play Pungi on Spotify",
        "pause the music then open my notes",
        "don't pause the music",
        "how do I stop music?",
    ],
)
def test_compound_specific_and_non_action_requests_are_not_shortcut(text):
    assert request_goal(text) is None


def test_media_resume_is_not_old_task_resume():
    assert resume_requested("resume the previous task")
    assert not resume_requested("resume music on VLC")
