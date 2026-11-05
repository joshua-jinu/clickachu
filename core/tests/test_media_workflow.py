"""Content selection, typo follow-ups, app grounding and no-progress recovery are generic."""

import asyncio
import json

import pytest

from clickachu.agent.goals import confirmed_context, request_goal
from clickachu.agent.orchestrator import Orchestrator
from clickachu.agent.progress import ReadProgress
from clickachu.api.protocol import ApproveMessage, RunMessage
from clickachu.llm.provider import Done, TextDelta, ToolCall
from clickachu.tools.base import Tier, Tool, ToolResult
from clickachu.tools.computer import DesktopActionArgs
from clickachu.tools.desktop import OpenAppArgs
from clickachu.tools.discovery import DiscoverToolsArgs, DiscoverToolsTool
from clickachu.tools.media import ControlMediaTool, MediaArgs
from clickachu.tools.registry import ToolRegistry, create_default_registry
from tests.fakes import FakeSecretStore, QueueProvider


@pytest.mark.parametrize("app", ["Spotify", "VLC", "Example Player"])
@pytest.mark.parametrize(
    "text",
    [
        "open {app} and find music Riptide and start playing it",
        "play Riptide by The Chainsmokers on {app}",
        "open {app} and play the song Riptide by The Chainsmokers",
    ],
)
def test_named_content_uses_selection_contract(app, text):
    goal = request_goal(text.format(app=app))
    assert goal.kind == "media_selection" and goal.target == app
    assert goal.content == "Riptide"
    assert not goal.permits("control_media", MediaArgs(app=app, action="play"))
    assert not goal.permits("open_app", OpenAppArgs(name="Unrelated App"))
    paused = ToolResult(
        ok=True, data={"status": "paused", "title": "Riptide", "artist": "The Chainsmokers"}
    )
    wrong = ToolResult(ok=True, data={"status": "playing", "title": "Pink Lips", "artist": "Other"})
    correct = ToolResult(
        ok=True, data={"status": "playing", "title": "Riptide", "artist": "The Chainsmokers"}
    )
    args = MediaArgs(app=app, action="status")
    assert not goal.completed("control_media", args, paused, True)
    assert not goal.completed("control_media", args, wrong, True)
    assert goal.completed("control_media", args, correct, True)


@pytest.mark.parametrize(
    "word,operation",
    [
        ("play", "play"),
        ("plsy", "play"),
        ("paly", "play"),
        ("pause", "pause"),
        ("paus", "pause"),
        ("stop", "pause"),
        ("resume", "play"),
    ],
)
def test_short_media_commands_use_confirmed_context(word, operation):
    goal = request_goal(word, {"media_app": "Any Player"})
    assert goal.kind == "media" and goal.operation == operation and goal.target == "Any Player"


def test_typo_recovery_does_not_rewrite_song_names_or_unrelated_commands():
    assert request_goal("plsy") is None
    assert request_goal("pay", {"media_app": "Any Player"}) is None
    assert request_goal("play Plsy by Someone on Any Player").content == "Plsy"
    assert request_goal("resume the previous task", {"media_app": "Any Player"}) is None


@pytest.mark.parametrize(
    "text,title,artist",
    [
        ("play In the End by Linkin Park", "In the End", "Linkin Park"),
        ("play Lean On by Major Lazer on Example Player", "Lean On", "Major Lazer"),
        ("play Dancing in the Dark", "Dancing in the Dark", ""),
        ('play "Stand by Me" on Example Player', "Stand by Me", ""),
    ],
)
def test_title_prepositions_and_quoted_by_are_not_app_or_artist_names(text, title, artist):
    goal = request_goal(text, {"media_app": "Example Player"})
    assert goal.target == "Example Player" and goal.content == title and goal.artist == artist


@pytest.mark.parametrize("app", ["Spotify", "VLC", "ExamplePlayer"])
async def test_bare_and_typo_media_followups_make_no_model_calls(settings_store, monkeypatch, app):
    # Reuse the real transport fixture, including Windows-state read-back.
    from types import SimpleNamespace

    from tests.test_media import Session

    session = Session()
    session.source_app_user_model_id = app + ".exe"

    async def manager():
        return SimpleNamespace(get_sessions=lambda: [session])

    monkeypatch.setattr("clickachu.tools.media.media_manager", manager)
    registry = ToolRegistry()
    registry.register(ControlMediaTool())
    provider = QueueProvider([[Done()]])
    runner, events = make_runner(settings_store, registry, provider)
    for i, text in enumerate((f"open {app} and play music", "pause", "plsy", "stop", "play")):
        await runner.handle_run(RunMessage(run_id=f"r{i}", conversation_id="same", text=text))
    finished = [e for e in events if e.type == "run.finished"]
    assert len(finished) == 5 and all(e.ok for e in finished)
    assert all(e.metrics.model_calls == 0 for e in finished)
    assert not provider.calls
    assert session.calls == ["play", "pause", "play", "pause", "play"]


class OpenFixture(Tool):
    name = "open_app"
    description = "Open fixture app"
    Args = OpenAppArgs
    tier = Tier.READ

    def __init__(self):
        self.apps = []

    def run(self, args, ctx):
        self.apps.append(args.name)
        return ToolResult(ok=True, summary=f"Opened {args.name}", data={"title": args.name})


class ClickFixture(Tool):
    name = "desktop_action"
    description = "Fixture desktop input"
    Args = DesktopActionArgs
    tier = Tier.READ

    def __init__(self):
        self.calls = []

    def run(self, args, ctx):
        self.calls.append(args)
        return ToolResult(ok=True, summary="Fresh results", data={"title": "Example Player"})


class MediaFixture(Tool):
    name = "control_media"
    description = "Read fixture transport"
    Args = MediaArgs
    tier = Tier.READ

    def __init__(self, click):
        self.click = click
        self.calls = []

    def run(self, args, ctx):
        self.calls.append(args)
        selected = any(a.action == "click" for a in self.click.calls)
        title = "Riptide" if selected else "Pink Lips"
        return ToolResult(
            ok=True,
            summary=f"{args.app}: playing — {title}.",
            data={"status": "playing", "title": title, "artist": "The Chainsmokers"},
        )

    def verify(self, args, result):
        return True


def make_runner(settings, registry, provider, workers=None):
    events = []

    async def emit(event):
        events.append(event)
        if event.type == "approval.request":
            asyncio.create_task(
                runner.handle_approve(
                    ApproveMessage(run_id=event.run_id, step_id=event.step_id, decision="approve")
                )
            )

    runner = Orchestrator(
        emit=emit,
        settings=settings,
        secrets=FakeSecretStore(),
        registry=registry,
        provider_factory=provider.factory,
        approval_timeout_s=1,
        workers=workers,
    )
    return runner, events


async def test_named_song_opens_ui_searches_selects_and_only_finishes_on_matching_track(
    settings_store,
):
    registry = ToolRegistry()
    opened, clicked = OpenFixture(), ClickFixture()
    media = MediaFixture(clicked)
    for tool in (opened, clicked, media):
        registry.register(tool)
    provider = QueueProvider(
        [
            [
                ToolCall(
                    "fill",
                    "desktop_action",
                    json.dumps(
                        {
                            "observation_id": "fixture",
                            "action": "fill",
                            "ref": "c1",
                            "text": "Riptide The Chainsmokers",
                            "submit": True,
                        }
                    ),
                ),
                Done(),
            ],
            [
                ToolCall(
                    "click",
                    "desktop_action",
                    json.dumps({"observation_id": "fixture", "action": "click", "ref": "c2"}),
                ),
                Done(),
            ],
        ]
    )
    runner, events = make_runner(settings_store, registry, provider)
    await runner.handle_run(
        RunMessage(run_id="selection", text="play Riptide by The Chainsmokers on Example Player")
    )
    finished = next(e for e in events if e.type == "run.finished")
    assert finished.ok and finished.metrics.model_calls == 2
    assert opened.apps == ["Example Player"] and len(clicked.calls) == 2
    assert len(media.calls) == 3 and all(a.action == "status" for a in media.calls)
    assert "Riptide" in finished.text and "Pink Lips" not in finished.text
    assert all(
        "control_media" not in {t["function"]["name"] for t in call["tools"]}
        for call in provider.calls
    )


async def test_old_queue_cannot_be_reported_as_named_song_success(settings_store):
    registry = ToolRegistry()
    opened, clicked = OpenFixture(), ClickFixture()
    for tool in (opened, clicked, MediaFixture(clicked)):
        registry.register(tool)
    provider = QueueProvider([[TextDelta("Played Riptide."), Done()]])
    runner, events = make_runner(settings_store, registry, provider)
    await runner.handle_run(RunMessage(run_id="wrong", text="play Riptide on Example Player"))
    finished = next(e for e in events if e.type == "run.finished")
    assert not finished.ok and "could not verify" in finished.text
    assert "Played Riptide." not in finished.text
    assert not clicked.calls


async def test_wrong_app_from_old_history_is_rejected_before_execution(settings_store):
    registry = ToolRegistry()
    opened = OpenFixture()
    registry.register(opened)
    provider = QueueProvider(
        [
            [ToolCall("wrong", "open_app", '{"name":"Notepad"}'), Done()],
            [TextDelta("Could not finish navigation."), Done()],
        ]
    )
    runner, events = make_runner(settings_store, registry, provider)
    await runner.handle_run(RunMessage(run_id="browser", text="open Chrome and open YouTube"))
    assert opened.apps == ["Chrome"]
    assert "Notepad" in str(provider.calls[-1]["messages"])
    assert "not requested" in str(provider.calls[-1]["messages"])


def test_compound_navigation_requires_destination_not_just_opening_browser():
    goal = request_goal("open Chrome and open YouTube")
    assert goal.kind == "app_destination"
    args = DesktopActionArgs(
        observation_id="fixture", action="fill", ref="c1", text="YouTube", submit=True
    )
    for document, expected in [
        ("New Tab", False),
        ("YouTube - Google Search", False),
        ("YouTube", True),
    ]:
        result = ToolResult(
            ok=True,
            data={
                "title": "YouTube - Google Chrome",
                "entries": [{"type": "Document", "text": document}],
            },
        )
        assert goal.completed("desktop_action", args, result, None) is expected
    assert not goal.completed(
        "open_app", OpenAppArgs(name="Chrome"), ToolResult(ok=True, data={"title": "Chrome"}), True
    )


def test_explicit_url_requires_matching_address_value_not_page_title_alone():
    goal = request_goal("open Example Browser and visit https://example.org/docs")
    args = DesktopActionArgs(
        observation_id="fixture",
        action="fill",
        ref="c1",
        text="https://example.org/docs",
        submit=True,
    )
    data = {
        "title": "Example Browser",
        "entries": [
            {"type": "Document", "text": "Documentation"},
            {"type": "Edit", "text": "Address and search bar", "value": "https://other.org/docs"},
        ],
    }
    assert not goal.completed("desktop_action", args, ToolResult(ok=True, data=data), None)
    data["entries"][1]["value"] = "https://example.org/docs"
    assert goal.completed("desktop_action", args, ToolResult(ok=True, data=data), None)


def test_discovery_of_unknown_app_exposes_general_ui_not_empty_success(db):
    registry = create_default_registry()
    discovery = registry.get("discover_tools")
    result = discovery.run(DiscoverToolsArgs(query="UnlistedPlayerName"), None)
    names = {s["function"]["name"] for s in result.data["schemas"]}
    assert result.ok and {"open_app", "observe_desktop", "desktop_action"} <= names


async def test_repeated_discovery_executes_once_then_stops_without_false_success(settings_store):
    registry = ToolRegistry()
    registry.register(OpenFixture())
    registry.register(DiscoverToolsTool(registry))
    provider = QueueProvider(
        [[ToolCall("discover", "discover_tools", '{"query":"UnknownApp"}'), Done()]]
    )
    runner, events = make_runner(settings_store, registry, provider)
    await runner.handle_run(RunMessage(run_id="loop", text="control an app", mode="agent"))
    assert len([e for e in events if e.type == "step.finished"]) == 1
    assert len(provider.calls) == 3
    assert any(e.type == "run.error" and "repeated action loop" in e.message for e in events)


def test_changing_snapshot_ids_are_not_new_information_but_changed_controls_are():
    progress, tool = ReadProgress(), ClickFixture()
    args = DesktopActionArgs(observation_id="fixture", action="click", ref="c1")
    for i in range(3):
        result = ToolResult(ok=True, data={"observation_id": str(i), "entries": [{"text": "Play"}]})
        assert progress.observe(tool, args, result) == i + 1
    assert (
        progress.observe(tool, args, ToolResult(ok=True, data={"entries": [{"text": "Pause"}]}))
        == 1
    )


def test_status_does_not_replace_confirmed_media_app_with_generic_alias():
    previous = {"media_app": "Spotify"}
    result = ToolResult(ok=True, data={"status": "paused"})
    assert (
        confirmed_context(
            previous, "control_media", MediaArgs(app="Music", action="status"), result, True
        )
        == previous
    )


async def test_live_refs_from_another_app_are_rejected_before_input(settings_store, monkeypatch):
    import time

    from clickachu.tools.computer import (
        DesktopActionTool,
        Observation,
        ObserveDesktopArgs,
        _observations,
    )

    class OpenScope(OpenFixture):
        def run(self, args, ctx):
            monkeypatch.setitem(
                _observations,
                "good",
                Observation(
                    ctx.run_id,
                    111,
                    (0, 0, 100, 100),
                    time.monotonic(),
                    10,
                    {"c1": ((1,), "back", "Button", "Back")},
                ),
            )
            return ToolResult(
                ok=True,
                data={
                    "hwnd": 111,
                    "title": "Example Browser",
                    "entries": [{"ref": "c1", "type": "Button", "text": "Back"}],
                },
            )

    class ObserveOther(Tool):
        name = "observe_desktop"
        description = "Fixture observation"
        Args = ObserveDesktopArgs
        tier = Tier.READ

        def run(self, args, ctx):
            monkeypatch.setitem(
                _observations,
                "other",
                Observation(
                    ctx.run_id,
                    222,
                    (0, 0, 100, 100),
                    time.monotonic(),
                    20,
                    {"c1": ((2,), "delete", "Button", "Delete")},
                ),
            )
            return ToolResult(
                ok=True,
                data={
                    "hwnd": 222,
                    "title": "Other app",
                    "entries": [{"ref": "c1", "type": "Button", "text": "Delete"}],
                },
            )

    calls = []

    def forbidden_input(self, args, ctx):
        calls.append(args)
        raise AssertionError("Wrong-app input must never run")

    monkeypatch.setattr(DesktopActionTool, "run", forbidden_input)
    registry = ToolRegistry()
    for tool in (OpenScope(), ObserveOther(), DesktopActionTool()):
        registry.register(tool)
    provider = QueueProvider(
        [
            [ToolCall("observe", "observe_desktop", '{"title":"Other app"}'), Done()],
            [
                ToolCall(
                    "input",
                    "desktop_action",
                    '{"observation_id":"other","action":"click","ref":"c1"}',
                ),
                Done(),
            ],
            [TextDelta("Cannot complete navigation."), Done()],
        ]
    )
    runner, events = make_runner(settings_store, registry, provider)
    await runner.handle_run(
        RunMessage(run_id="scope", text="open Example Browser and visit Example Docs")
    )
    assert calls == [] and not any(e.type == "approval.request" for e in events)
    assert "different application" in str(provider.calls[-1]["messages"])


async def test_unambiguous_native_song_search_and_result_need_no_model_call(
    settings_store, monkeypatch
):
    import time

    from clickachu.tools.base import ToolWorkers
    from clickachu.tools.computer import DesktopActionTool, Observation, _observations
    from tests.conftest import run_inline

    actions = []

    def snapshot(ctx, searched=False):
        entries = [{"ref": "c1", "type": "ComboBox", "text": "Search all media"}]
        if searched:
            entries.append(
                {"ref": "c2", "type": "Button", "text": "Play Example Song by Example Artist"}
            )
        key = "search-result" if searched else "search-box"
        controls = {e["ref"]: ((i,), e["ref"], e["type"], e["text"]) for i, e in enumerate(entries)}
        monkeypatch.setitem(
            _observations,
            key,
            Observation(ctx.run_id, 111, (0, 0, 100, 100), time.monotonic(), 10, controls),
        )
        return ToolResult(ok=True, data={"hwnd": 111, "title": "Any Player", "entries": entries})

    class OpenNative(OpenFixture):
        def run(self, args, ctx):
            return snapshot(ctx)

    def input_fixture(self, args, ctx):
        actions.append(args)
        return snapshot(ctx, searched=True)

    monkeypatch.setattr(DesktopActionTool, "run", input_fixture)

    class Metadata(MediaFixture):
        def run(self, args, ctx):
            selected = any(a.action == "click" for a in actions)
            return ToolResult(
                ok=True,
                summary="Requested song playing" if selected else "Old queue paused",
                data={
                    "title": "Example Song" if selected else "Old song",
                    "artist": "Example Artist",
                    "status": "playing" if selected else "paused",
                },
            )

    registry = ToolRegistry()
    for tool in (OpenNative(), DesktopActionTool(), Metadata(None)):
        registry.register(tool)
    provider = QueueProvider([[Done()]])
    runner, events = make_runner(settings_store, registry, provider, ToolWorkers(com=run_inline))
    await runner.handle_run(
        RunMessage(run_id="automatic", text="play Example Song by Example Artist on Any Player")
    )
    finished = next(e for e in events if e.type == "run.finished")
    assert finished.ok, provider.calls[-1]['messages'][-6:] if provider.calls else events
    assert finished.metrics.model_calls == 0 and not provider.calls
    assert [a.action for a in actions] == ["fill", "click"]
    assert actions[0].text == "Example Song Example Artist" and actions[0].submit
