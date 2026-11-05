"""Hybrid execution boundaries, recovery and provider-independent activity."""

import asyncio

import pytest
from pydantic import BaseModel

from clickachu.agent.orchestrator import Orchestrator
from clickachu.agent.tasks import TaskStore, compact_messages
from clickachu.api.protocol import RunMessage, Settings, SettingsUpdate, Usage
from clickachu.llm.capabilities import capabilities
from clickachu.llm.provider import Done, LLMError, TextDelta, ToolCall
from clickachu.projects.roots import RootStore
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult
from clickachu.tools.computer import (
    DesktopActionArgs,
    DesktopActionTool,
    Observation,
    _observations,
)
from clickachu.tools.execution import ExecuteCodeArgs, ExecuteCodeTool, task_workspace
from clickachu.tools.policy import PolicyAction, decide
from clickachu.tools.registry import ToolRegistry, create_default_registry
from clickachu.tools.workspace import WorkspaceFilesArgs, WorkspaceFilesTool
from tests.fakes import FakeSecretStore, QueueProvider


def context(db, **kwargs):
    return ToolContext(
        run_id="run",
        conversation_id="conversation",
        step_id="step",
        snapshot=None,
        db=db,
        settings=Settings(**kwargs),
    )


def test_host_scripts_never_inherit_always_approval(db, tmp_path):
    root = RootStore(db).add(str(tmp_path), can_write=True)
    args = ExecuteCodeArgs(code="print(1)", purpose="test", environment="host", cwd=str(tmp_path))
    decision = decide(ExecuteCodeTool(), args, roots=[root], trusted={("execute_code", "*")})
    assert decision.action == PolicyAction.ASK
    assert (
        decide(ExecuteCodeTool(), ExecuteCodeArgs(code="print(1)", purpose="test")).action
        == PolicyAction.AUTO
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"language": "powershell"},
        {"cwd": "C:/host"},
        {"environment": "host"},
    ],
)
def test_invalid_execution_scope_is_rejected(patch):
    with pytest.raises(ValueError):
        ExecuteCodeArgs(code="print(1)", purpose="test", **patch)


async def test_disabled_host_does_not_execute(db, tmp_path):
    args = ExecuteCodeArgs(
        code="raise AssertionError('ran')", purpose="test", environment="host", cwd=str(tmp_path)
    )
    result = await ExecuteCodeTool().run(args, context(db))
    assert not result.ok and "disabled" in result.summary


async def test_host_code_preserves_quotes_and_limits_output(db, tmp_path, monkeypatch):
    RootStore(db).add(str(tmp_path), can_write=True)
    monkeypatch.setenv("CLICKACHU_TEST_SECRET", "do-not-inherit")
    result = await ExecuteCodeTool().run(
        ExecuteCodeArgs(
            purpose="write and inspect a fixture",
            environment="host",
            cwd=str(tmp_path),
            code="import os\nfrom pathlib import Path\n"
            "assert 'CLICKACHU_TEST_SECRET' not in os.environ\n"
            "Path('result.txt').write_text('literal $() ` quotes', encoding='utf-8')\n"
            "print('x' * 30000)\n",
        ),
        context(db, host_execution_enabled=True),
    )
    assert result.ok, result.llm_text()
    assert (tmp_path / "result.txt").read_text() == "literal $() ` quotes"
    assert "[output truncated]" in result.data["stdout"]
    assert len(result.data["stdout"]) < 17000


async def test_host_timeout_reports_partial_changes(db, tmp_path):
    RootStore(db).add(str(tmp_path), can_write=True)
    result = await ExecuteCodeTool().run(
        ExecuteCodeArgs(
            purpose="timeout fixture",
            environment="host",
            cwd=str(tmp_path),
            timeout_seconds=1,
            code="import time\nfrom pathlib import Path\n"
            "Path('partial.txt').write_text('partial')\ntime.sleep(20)",
        ),
        context(db, host_execution_enabled=True),
    )
    assert not result.ok and "partial changes" in result.summary
    assert (tmp_path / "partial.txt").exists()


async def test_isolated_missing_docker_never_falls_back_to_host(db, monkeypatch):
    monkeypatch.setattr("clickachu.tools.execution.shutil.which", lambda name: None)
    result = await ExecuteCodeTool().run(
        ExecuteCodeArgs(code="raise AssertionError('ran')", purpose="test"), context(db)
    )
    assert not result.ok and "no host fallback" in result.summary


def test_workspace_transfers_require_allowed_paths_and_no_overwrites(db, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    RootStore(db).add(str(root), can_write=True)
    source = root / "input.txt"
    source.write_text("fixture")
    ctx = context(db)
    tool = WorkspaceFilesTool()
    assert tool.run(
        WorkspaceFilesArgs(operation="import", name="input.txt", path=str(source)), ctx
    ).ok
    output = root / "export.txt"
    args = WorkspaceFilesArgs(operation="export", name="input.txt", path=str(output))
    assert (
        decide(tool, args, roots=RootStore(db).list(), trusted={(tool.name, "*")}).action
        == PolicyAction.ASK
    )
    assert tool.run(args, ctx).ok
    assert output.read_text() == "fixture"
    with pytest.raises(FileExistsError):
        tool.run(args, ctx)
    assert not tool.run(WorkspaceFilesArgs(operation="read", name="../secret"), ctx).ok
    assert not tool.run(
        WorkspaceFilesArgs(operation="import", name="x", path=str(tmp_path / "secret")), ctx
    ).ok
    assert task_workspace(context(db)) == task_workspace(ctx)


def test_task_checkpoint_roundtrip_and_compaction_keep_tool_pairs(db):
    TaskStore(db).save("conversation", "run", {"objective": "test", "pending_action": "inspect"})
    assert TaskStore(db).get("conversation")["pending_action"] == "inspect"
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "request"}]
    for i in range(8):
        messages += [
            {"role": "assistant", "tool_calls": [{"id": str(i)}]},
            {"role": "tool", "tool_call_id": str(i), "content": "result"},
        ]
    compacted = compact_messages(messages)
    assert len(compacted) == 8 and compacted[:2] == messages[:2]
    for i in range(2, len(compacted), 2):
        assert compacted[i]["tool_calls"][0]["id"] == compacted[i + 1]["tool_call_id"]


def test_vision_requires_known_model_or_explicit_opt_in():
    assert capabilities(Settings(), "gemini-2.5-flash").images
    assert not capabilities(Settings(provider="ollama"), "unknown").images
    assert capabilities(Settings(provider="ollama", vision_mode="on"), "unknown").images


def test_discovery_keeps_initial_request_small(db):
    registry = create_default_registry()
    initial = registry.hybrid_schemas("organize these documents")
    assert len(initial) < len(registry.schemas()) / 2
    result = registry.get("discover_tools").run(
        registry.get("discover_tools").Args(query="workspace"), context(db)
    )
    assert "workspace_files" in {s["function"]["name"] for s in result.data["schemas"]}


def test_desktop_rejects_stale_and_other_run_observations(db):
    _observations["stale"] = Observation("run", 123, (0, 0, 200, 200), -1000, 456)
    _observations["other"] = Observation("other-run", 123, (0, 0, 200, 200), 0, 456)
    tool = DesktopActionTool()
    for key in ("stale", "other", "missing"):
        result = tool.run(
            DesktopActionArgs(observation_id=key, action="click", x=1, y=1), context(db)
        )
        assert not result.ok


class EmptyArgs(BaseModel):
    pass


class ScreenshotFixture(Tool):
    name = "screenshot_fixture"
    description = "Fake screenshot"
    Args = EmptyArgs
    tier = Tier.READ

    def run(self, args, ctx):
        return ToolResult(
            ok=True, summary="Screenshot fixture", images=("data:image/png;base64,AA==",)
        )


async def run_agent(settings_store, provider, registry, **message):
    events = []

    async def emit(event):
        events.append(event)

    agent = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=provider.factory,
        registry=registry,
    )
    await agent.handle_run(RunMessage(run_id="run", text="inspect", mode="agent", **message))
    return agent, events


async def test_images_follow_all_tool_replies(settings_store):
    settings_store.update(SettingsUpdate(vision_mode="on"))
    registry = ToolRegistry()
    registry.register(ScreenshotFixture())
    provider = QueueProvider(
        [
            [
                ToolCall("a", "screenshot_fixture", "{}"),
                ToolCall("b", "screenshot_fixture", "{}"),
                Done(),
            ],
            [TextDelta("Inspected."), Done()],
        ]
    )
    _, events = await run_agent(settings_store, provider, registry)
    messages = provider.calls[-1]["messages"]
    calls_index = next(i for i, m in enumerate(messages) if m.get("tool_calls"))
    assert [m["role"] for m in messages[calls_index : calls_index + 4]] == [
        "assistant",
        "tool",
        "tool",
        "user",
    ]
    assert events[-2].metrics.model_calls == 2
    assert events[-2].metrics.actions == 2


async def test_model_failure_keeps_pending_task_evidence(settings_store):
    class FailProvider(QueueProvider):
        async def stream(self, *args, **kwargs):
            if False:
                yield
            raise LLMError("fixture failure")

    await run_agent(settings_store, FailProvider([]), ToolRegistry(), conversation_id="checkpoint")
    state = TaskStore(settings_store.db).get("checkpoint")
    assert state["status"] == "interrupted"
    assert "fixture failure" in state["last_response"]


async def test_repeated_failures_stop_without_false_success(settings_store):
    class Broken(ScreenshotFixture):
        def run(self, args, ctx):
            return ToolResult(ok=False, summary="fixture failure")

    registry = ToolRegistry()
    registry.register(Broken())
    provider = QueueProvider([[ToolCall("a", "screenshot_fixture", "{}"), Done()]])
    _, events = await run_agent(settings_store, provider, registry)
    assert len(provider.calls) == settings_store.get().agent_max_failures
    assert events[-2].ok is False and "repeated failures" in events[-2].text


async def test_output_budget_stops_before_next_model_call(settings_store):
    settings_store.update(SettingsUpdate(agent_max_output_tokens=256))
    registry = ToolRegistry()
    registry.register(ScreenshotFixture())
    provider = QueueProvider(
        [[ToolCall("a", "screenshot_fixture", "{}"), Done(Usage(output_tokens=256))]]
    )
    _, events = await run_agent(settings_store, provider, registry)
    assert len(provider.calls) == 1 and "output token budget" in events[-2].text


async def test_cancelled_host_script_terminates_its_child(db, tmp_path):
    import psutil

    RootStore(db).add(str(tmp_path), can_write=True)
    task = asyncio.create_task(
        ExecuteCodeTool().run(
            ExecuteCodeArgs(
                purpose="cancellation fixture",
                environment="host",
                cwd=str(tmp_path),
                code="import subprocess, sys, time\nfrom pathlib import Path\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "Path('child.pid').write_text(str(child.pid))\ntime.sleep(30)",
            ),
            context(db, host_execution_enabled=True),
        )
    )
    pid_file = tmp_path / "child.pid"
    for _ in range(100):
        if pid_file.exists() or task.done():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        if not psutil.pid_exists(pid):
            break
        await asyncio.sleep(0.02)
    assert not psutil.pid_exists(pid)


async def test_continue_reloads_checkpoint_without_replaying_tools(settings_store):
    TaskStore(settings_store.db).save(
        "resume",
        "old-run",
        {
            "objective": "organize fixtures",
            "status": "interrupted",
            "evidence": [{"tool": "write_file", "summary": "created existing.txt"}],
            "pending_action": None,
        },
    )
    provider = QueueProvider([[TextDelta("Existing progress is saved."), Done()]])
    events = []

    async def emit(event):
        events.append(event)

    agent = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=provider.factory,
        registry=ToolRegistry(),
    )
    await agent.handle_run(
        RunMessage(run_id="resume-run", conversation_id="resume", text="continue")
    )
    assert events[0].mode == "agent"
    assert "created existing.txt" in str(provider.calls[0]["messages"])
    assert not any(event.type == "step.started" for event in events)
    assert TaskStore(settings_store.db).get("resume")["objective"] == "organize fixtures"


@pytest.mark.parametrize(
    "model,effort,expected",
    [
        ("gemini-2.5-flash", "off", "none"),
        ("gemini-2.5-pro", "off", "low"),
        ("gemini-3-pro", "off", "low"),
        ("gemini-2.5-flash", "high", "high"),
    ],
)
async def test_provider_reasoning_options_are_model_aware(model, effort, expected):
    from types import SimpleNamespace

    from clickachu.llm.openrouter import OpenRouterProvider

    requests = []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def __aiter__(self):
            if False:
                yield

    async def create(**kwargs):
        requests.append(kwargs)
        return Stream()

    provider = OpenRouterProvider(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key="fixture",
        reasoning_effort=effort,
    )
    real_client = provider._client
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    try:
        _ = [event async for event in provider.stream(model, [])]
        assert requests[0]["reasoning_effort"] == expected
        if effort != "off":
            config = requests[0]["extra_body"]["extra_body"]["google"]["thinking_config"]
            assert config["include_thoughts"]
    finally:
        await real_client.close()


async def test_isolated_runtime_keeps_host_mounts_and_network_restricted(db, monkeypatch):
    from types import SimpleNamespace

    calls = []

    async def fake_process(*command, **kwargs):
        calls.append((command, kwargs))
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_eof()
        stderr.feed_eof()

        async def wait():
            return 0

        return SimpleNamespace(pid=999999, returncode=0, stdout=stdout, stderr=stderr, wait=wait)

    monkeypatch.setattr("clickachu.tools.execution.shutil.which", lambda name: "docker")
    monkeypatch.setattr("clickachu.tools.execution.attach_job", lambda pid: None)
    monkeypatch.setattr("clickachu.tools.execution.asyncio.create_subprocess_exec", fake_process)
    result = await ExecuteCodeTool().run(
        ExecuteCodeArgs(code="print('fixture')", purpose="sandbox fixture"), context(db)
    )
    assert result.ok
    command = calls[0][0]
    for option in ("--network=none", "--read-only", "--cap-drop=ALL", "--pids-limit=64"):
        assert option in command
    mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
    assert len(mounts) == 2
    assert any("target=/workspace" in mount for mount in mounts)
    assert any("target=/run/task.py,readonly" in mount for mount in mounts)
    assert calls[-1][0][1:3] == ("rm", "-f")


async def test_final_answer_after_failed_action_is_not_marked_completed(settings_store):
    class Broken(ScreenshotFixture):
        def run(self, args, ctx):
            return ToolResult(ok=False, summary="fixture failure")

    registry = ToolRegistry()
    registry.register(Broken())
    provider = QueueProvider(
        [
            [ToolCall("a", "screenshot_fixture", "{}"), Done()],
            [TextDelta("The action failed."), Done()],
        ]
    )
    _, events = await run_agent(settings_store, provider, registry)
    assert events[-2].status == "error" and not events[-2].ok
