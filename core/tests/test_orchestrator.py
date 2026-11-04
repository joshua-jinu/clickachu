"""Router and Agent orchestrator tests (PLAN §11, §12, §18)."""

from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from clickachu.db.conn import Database
from clickachu.llm.provider import Done, TextDelta, ToolCall
from clickachu.llm.router import heuristic_mode, route_mode
from clickachu.projects.roots import RootStore
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult
from clickachu.tools.registry import ToolRegistry, create_default_registry
from tests.conftest import AUTH, BASE_URL, TOKEN, WS_URL, AppFactory
from tests.fakes import FakeProvider, Hang, QueueProvider
from tests.test_ws import receive_until


class DeployArgs(BaseModel):
    service: str = Field(min_length=1)
    env: str = "dev"


class ConfirmDeployTool(Tool[DeployArgs]):
    name: ClassVar[str] = "deploy_service"
    description: ClassVar[str] = "Deploy a service (requires confirmation)"
    Args: ClassVar[type[BaseModel]] = DeployArgs
    tier: ClassVar[Tier] = Tier.CONFIRM

    def __init__(self) -> None:
        self.calls: list[DeployArgs] = []
        self.cleaned_up = False

    def preview(self, args: DeployArgs) -> str:
        return f"Deploy {args.service} to {args.env}"

    def trust_key(self, args: DeployArgs) -> str | None:
        return f"{args.service}:{args.env}"

    def run(self, args: DeployArgs, ctx: ToolContext) -> ToolResult:
        self.calls.append(args)
        ctx.unverified_cleanup.append(self._cleanup)
        return ToolResult(
            ok=True,
            data={"service": args.service, "env": args.env},
            summary=f"Deployed {args.service} ({args.env})",
        )

    def verify(self, args: DeployArgs, result: ToolResult) -> bool | None:
        del args
        return result.ok

    def _cleanup(self) -> None:
        self.cleaned_up = True


class BlockedDangerTool(Tool[DeployArgs]):
    name: ClassVar[str] = "danger_shell"
    description: ClassVar[str] = "Blocked tool"
    Args: ClassVar[type[BaseModel]] = DeployArgs
    tier: ClassVar[Tier] = Tier.BLOCKED

    def run(self, args: DeployArgs, ctx: ToolContext) -> ToolResult:
        del args, ctx
        raise AssertionError("blocked tool must never run")


# ---------------------------------------------------------------- Router (§11)


@pytest.mark.parametrize(
    ("text", "mode", "action", "expected"),
    [
        ("hello", "agent", None, "agent"),
        ("open notepad", "ask", None, "ask"),
        ("check this", None, "fix", "agent"),
        ("do this", None, "do", "agent"),
        ("check this", None, "summarise", "ask"),
        ("Find files named resume", None, None, "agent"),
        ("Please open Notepad and write hello", None, None, "agent"),
        ("Start TeamFuse", None, None, "agent"),
        ("Search the web for ioredis retry strategy", None, None, "agent"),
        (
            '"List all my open windows, find Notepad (or Edge), and bring it to the foreground."',
            None,
            None,
            "agent",
        ),
        ('"Create a new file named test_notes.md in my workspace root"', None, None, "agent"),
        ("What is a Windows Job Object?", None, None, None),
        ("hello, what model are you", None, None, "ask"),
        ("what model are you", None, None, "ask"),
        ("hi", None, None, "ask"),
        ("who are you", None, None, "ask"),
        ("can you oppen notepad and write a recepie for choco chip cookie and then save it as ultimate chocochip cookie in my onedrive desktop", None, None, "agent"),
        ("okay do it", None, None, "agent"),
        ("do it", None, None, "agent"),
    ],
)
def test_router_heuristics(
    text: str, mode: Any, action: Any, expected: str | None
) -> None:
    assert heuristic_mode(text, mode=mode, action=action) == expected


@pytest.mark.asyncio
async def test_router_llm_call_and_timeout_fallback() -> None:
    provider = FakeProvider([TextDelta('{"mode": "agent"}'), Done()])
    mode = await route_mode(
        "Could you help me with my resume?",
        provider=provider,
        router_model="test/router",
    )
    assert mode == "agent"

    hanging = FakeProvider([Hang()])
    fallback = await route_mode(
        "What is the capital of France?",
        provider=hanging,
        router_model="test/router",
        timeout_s=0.05,
    )
    assert fallback == "ask"


@pytest.mark.asyncio
async def test_router_ollama_fast_path() -> None:
    # Under Ollama, queries that don't trigger agent heuristics must immediately resolve to 'ask'
    # without making any router LLM call (which would hang here).
    hanging = FakeProvider([Hang()])
    mode = await route_mode(
        "What is a Windows Job Object?",
        provider=hanging,
        router_model="ollama/llama3.2",
        is_ollama=True,
    )
    assert mode == "ask"


def test_build_initial_messages_minimal_tokens_for_greetings() -> None:
    from datetime import UTC, datetime
    from clickachu.agent.orchestrator import _build_initial_messages
    from clickachu.api.protocol import ContextSnapshot, Memory, RunMessage, Settings

    huge_selection = "def some_code():\n    pass\n" * 500  # 15,000+ chars
    snapshot = ContextSnapshot(
        id="ctx_1",
        captured_at=datetime.now(UTC),
        hwnd=123,
        process="code.exe",
        app="VS Code",
        window_title="editor.py - VS Code",
        selection=huge_selection,
        selection_method="clipboard",
        selection_truncated=False,
        is_terminal=False,
    )
    settings = Settings(provider="ollama", ollama_model="llama3.2:3b")
    msg = RunMessage(run_id="run_1", text="hello, what model are you")
    memories = [
        Memory(
            id=1,
            content="User likes python",
            scope_type="global",
            scope_key="",
            category="fact",
            pinned=False,
            enabled=True,
            use_count=0,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
    ]

    messages = _build_initial_messages(
        msg,
        settings,
        snapshot,
        memories=memories,
        is_ollama=True,
        model_name="llama3.2:3b",
    )
    # The prompt should NOT include the huge selection or unneeded memories for a simple greeting
    full_text = " ".join(str(m.get("content", "")) for m in messages)
    assert "def some_code():" not in full_text
    assert "User likes python" not in full_text
    assert "running llama3.2:3b" in full_text
    assert len(full_text) < 1500  # Very small prompt (~200 tokens), minimum tokens!

    # But when asking about code/selection, the selection IS included (and capped for Ollama)
    msg_code = RunMessage(run_id="run_2", text="explain this code")
    messages_code = _build_initial_messages(
        msg_code,
        settings,
        snapshot,
        is_ollama=True,
        model_name="llama3.2:3b",
    )
    full_code_text = " ".join(str(m.get("content", "")) for m in messages_code)
    assert "def some_code():" in full_code_text
    # And capped at 3,500 chars (+ context overhead) rather than 15,000 chars
    assert len(full_code_text) < 5500


# ---------------------------------------------------------------- Orchestrator (§12)


def test_agent_auto_tool_search_files_and_log(
    make_app: AppFactory, db: Database, tmp_path: Path
) -> None:
    RootStore(db).add(str(tmp_path), can_write=False)
    resume = tmp_path / "resume_2026.pdf"
    resume.write_text("cv", encoding="utf-8")

    provider = QueueProvider(
        [
            [
                TextDelta("Searching for your resume. "),
                ToolCall("call_1", "search_files", '{"query": "resume"}'),
                Done(),
            ],
            [TextDelta(f"Found `{resume.name}`."), Done()],
        ]
    )
    app = make_app(provider=provider)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r_search", "text": "Find files named resume"})
            events = receive_until(ws, "run.finished")
            events.append(ws.receive_json())  # trailing orb.state: idle

        log_rows = client.get("/log", params={"run_id": "r_search"}).json()

    types = [e["type"] for e in events]
    assert types == [
        "run.started",
        "orb.state",
        "run.detail",
        "token",
        "step.started",
        "orb.state",
        "step.finished",
        "orb.state",
        "token",
        "run.finished",
        "orb.state",
    ]
    assert events[0] == {"type": "run.started", "run_id": "r_search", "mode": "agent"}
    assert [e["state"] for e in events if e["type"] == "orb.state"] == [
        "thinking",
        "acting",
        "thinking",
        "idle",
    ]
    step_done = next(e for e in events if e["type"] == "step.finished")
    assert step_done["ok"] is True
    assert "1 file(s)" in step_done["summary"]

    # Tool output reaching the LLM on step 2 is wrapped as untrusted_data.
    second_call_msgs = provider.calls[1]["messages"]
    tool_msg = next(m for m in second_call_msgs if m["role"] == "tool")
    assert '<untrusted_data source="tool:search_files">' in tool_msg["content"]
    assert "resume_2026.pdf" in tool_msg["content"]

    assert len(log_rows) == 1
    assert log_rows[0]["tool"] == "search_files"
    assert log_rows[0]["decision"] == "auto"
    assert log_rows[0]["ok"] is True


def test_agent_approval_approve_deny_always_and_timeout(make_app: AppFactory) -> None:
    deploy = ConfirmDeployTool()
    reg = create_default_registry()
    reg.register(deploy)

    provider = QueueProvider(
        [
            # Run 1: user approves once
            [
                ToolCall("s_approve", "deploy_service", '{"service": "api", "env": "staging"}'),
                Done(),
            ],
            [TextDelta("Deployed api."), Done()],
            # Run 2: user denies
            [ToolCall("s_deny", "deploy_service", '{"service": "api", "env": "prod"}'), Done()],
            [TextDelta("Understood, cancelled deploy."), Done()],
            # Run 3: user clicks Always for worker:dev
            [
                ToolCall("s_always1", "deploy_service", '{"service": "worker", "env": "dev"}'),
                Done(),
            ],
            [TextDelta("Worker deployed."), Done()],
            # Run 4: same worker:dev runs automatically without approval.request
            [
                ToolCall("s_always2", "deploy_service", '{"service": "worker", "env": "dev"}'),
                Done(),
            ],
            [TextDelta("Worker redeployed automatically."), Done()],
        ]
    )
    app = make_app(provider=provider, registry=reg)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})

            # 1. Approve
            ws.send_json({"type": "run", "run_id": "r1", "text": "deploy", "mode": "agent"})
            pre = receive_until(ws, "approval.request")
            req = pre[-1]
            assert req["tool"] == "deploy_service"
            assert req["preview"] == "Deploy api to staging"
            assert ws.receive_json() == {"type": "orb.state", "state": "attention"}
            ws.send_json(
                {"type": "approve", "run_id": "r1", "step_id": "s_approve", "decision": "approve"}
            )
            post = receive_until(ws, "run.finished")
            ws.receive_json()  # idle
            step_fin = next(e for e in post if e["type"] == "step.finished")
            assert step_fin["ok"] is True and step_fin["verified"] is True
            assert len(deploy.calls) == 1

            # 2. Deny
            ws.send_json({"type": "run", "run_id": "r2", "text": "deploy prod", "mode": "agent"})
            receive_until(ws, "approval.request")
            ws.receive_json()  # attention
            ws.send_json(
                {"type": "approve", "run_id": "r2", "step_id": "s_deny", "decision": "deny"}
            )
            post_deny = receive_until(ws, "run.finished")
            ws.receive_json()  # idle
            deny_fin = next(e for e in post_deny if e["type"] == "step.finished")
            assert deny_fin["ok"] is False
            assert "denied" in deny_fin["summary"]
            assert len(deploy.calls) == 1  # Not called on deny

            # 3. Always
            ws.send_json({"type": "run", "run_id": "r3", "text": "deploy worker", "mode": "agent"})
            receive_until(ws, "approval.request")
            ws.receive_json()  # attention
            ws.send_json(
                {"type": "approve", "run_id": "r3", "step_id": "s_always1", "decision": "always"}
            )
            receive_until(ws, "run.finished")
            ws.receive_json()  # idle
            assert len(deploy.calls) == 2

            # 4. Subsequent run with same trust_key ("worker:dev") skips approval.request
            ws.send_json({"type": "run", "run_id": "r4", "text": "deploy worker", "mode": "agent"})
            r4_events = receive_until(ws, "run.finished")
            ws.receive_json()  # idle
            assert not [e for e in r4_events if e["type"] == "approval.request"]
            assert len(deploy.calls) == 3

        assert client.get("/log", params={"run_id": "r1"}).json()[0]["decision"] == "approved"
        assert client.get("/log", params={"run_id": "r2"}).json()[0]["decision"] == "denied"
        assert client.get("/log", params={"run_id": "r3"}).json()[0]["decision"] == "always"
        assert client.get("/log", params={"run_id": "r4"}).json()[0]["decision"] == "always"


def test_agent_approval_timeout_denies(make_app: AppFactory) -> None:
    deploy = ConfirmDeployTool()
    reg = ToolRegistry()
    reg.register(deploy)
    provider = QueueProvider(
        [
            [ToolCall("s_to", "deploy_service", '{"service": "api"}'), Done()],
            [TextDelta("Timed out."), Done()],
        ]
    )
    app = make_app(provider=provider, registry=reg, approval_timeout_s=0.05)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r_to", "text": "deploy", "mode": "agent"})
            events = receive_until(ws, "run.finished")
            ws.receive_json()  # idle
        log_entry = client.get("/log", params={"run_id": "r_to"}).json()[0]

    step_fin = next(e for e in events if e["type"] == "step.finished")
    assert step_fin["ok"] is False
    assert "timed out" in step_fin["summary"]
    assert deploy.calls == []
    assert log_entry["decision"] == "denied"


def test_agent_validation_retry_once_then_succeeds(make_app: AppFactory, db: Database) -> None:
    provider = QueueProvider(
        [
            # 1st attempt: empty query fails RecallArgs(min_length=1)
            [ToolCall("c_bad", "recall", '{"query": ""}'), Done()],
            # 2nd attempt (after validation error sent back): valid query
            [ToolCall("c_good", "recall", '{"query": "pnpm"}'), Done()],
            [TextDelta("Done."), Done()],
        ]
    )
    del db
    app = make_app(provider=provider)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r_val", "text": "check", "mode": "agent"})
            events = receive_until(ws, "run.finished")
            ws.receive_json()  # idle

    # Only the valid 2nd call started a step; the 1st returned a validation error to the model.
    started = [e for e in events if e["type"] == "step.started"]
    assert len(started) == 1 and started[0]["step_id"] == "c_good"
    assert events[-1]["ok"] is True


def test_agent_validation_stops_after_second_failure(make_app: AppFactory) -> None:
    provider = FakeProvider([ToolCall("c_bad", "recall", '{"query": ""}'), Done()])
    app = make_app(provider=provider)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r_val2", "text": "check", "mode": "agent"})
            events = receive_until(ws, "run.finished")
            ws.receive_json()  # idle

    assert len(provider.calls) == 2
    assert "clarify" in events[-1]["text"].lower()


def test_agent_step_cap_stops_at_8_steps(make_app: AppFactory) -> None:
    provider = FakeProvider([ToolCall("c_loop", "recall", '{"query": "pnpm"}'), Done()])
    app = make_app(provider=provider)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r_cap", "text": "loop", "mode": "agent"})
            events = receive_until(ws, "run.finished")
            ws.receive_json()  # idle

    started = [e for e in events if e["type"] == "step.started"]
    finished = [e for e in events if e["type"] == "step.finished"]
    assert len(started) == 8
    assert len(finished) == 8
    assert len(provider.calls) == 8
    assert "8-step limit" in events[-1]["text"]


def test_agent_cancel_during_approval(make_app: AppFactory) -> None:
    deploy = ConfirmDeployTool()
    reg = ToolRegistry()
    reg.register(deploy)
    provider = FakeProvider(
        [ToolCall("s_cancel", "deploy_service", '{"service": "api"}'), Done()]
    )
    app = make_app(provider=provider, registry=reg)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r_can", "text": "deploy", "mode": "agent"})
            receive_until(ws, "approval.request")
            ws.receive_json()  # attention
            ws.send_json({"type": "cancel", "run_id": "r_can"})
            post = receive_until(ws, "run.finished")
            idle = ws.receive_json()

    assert post[-1]["ok"] is False
    assert idle == {"type": "orb.state", "state": "idle"}
    assert deploy.calls == []


def test_agent_blocked_tool_logged_and_reported_to_llm(make_app: AppFactory) -> None:
    reg = create_default_registry()
    reg.register(BlockedDangerTool())
    provider = QueueProvider(
        [
            [ToolCall("s_blk", "danger_shell", '{"service": "rm"}'), Done()],
            [TextDelta("That tool is blocked by policy."), Done()],
        ]
    )
    app = make_app(provider=provider, registry=reg)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r_blk", "text": "danger", "mode": "agent"})
            events = receive_until(ws, "run.finished")
            ws.receive_json()  # idle
        log_entry = client.get("/log", params={"run_id": "r_blk"}).json()[0]

    step_fin = next(e for e in events if e["type"] == "step.finished")
    assert step_fin["ok"] is False
    assert step_fin["summary"].startswith("blocked by policy:")
    assert log_entry["decision"] == "blocked"
    assert log_entry["ok"] is False


def test_multiturn_conversation_preserves_history(make_app: AppFactory) -> None:
    provider = QueueProvider(
        [
            [TextDelta("Got it! Vim is your favorite."), Done()],
            [TextDelta("Your favorite editor is Vim."), Done()],
        ]
    )
    app = make_app(provider=provider)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            # First turn in session
            ws.send_json(
                {
                    "type": "run",
                    "run_id": "r_turn1",
                    "conversation_id": "conv_editor",
                    "text": "My favorite editor is Vim",
                    "mode": "ask",
                }
            )
            receive_until(ws, "run.finished")
            ws.receive_json()  # idle

            # Second turn in same session
            ws.send_json(
                {
                    "type": "run",
                    "run_id": "r_turn2",
                    "conversation_id": "conv_editor",
                    "text": "What is my favorite editor?",
                    "mode": "ask",
                }
            )
            receive_until(ws, "run.finished")
            ws.receive_json()  # idle

    # Check the 2nd provider call had the 1st turn in messages
    call2_messages = provider.calls[1]["messages"]
    roles_and_content = [(m["role"], m["content"]) for m in call2_messages]
    assert ("user", "My favorite editor is Vim") in roles_and_content
    assert ("assistant", "Got it! Vim is your favorite.") in roles_and_content


def test_agent_nudged_when_promising_action_without_tool_call(make_app: AppFactory) -> None:
    provider = QueueProvider(
        [
            # Turn 1: Model promises action in text without calling a tool
            [TextDelta("To proceed, I will write the file test_notes.md."), Done()],
            # Turn 2: Nudged, model now emits the tool call
            [ToolCall("c_nudge", "recall", '{"query": "notes"}'), Done()],
            [TextDelta("All done!"), Done()],
        ]
    )
    app = make_app(provider=provider)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json(
                {
                    "type": "run",
                    "run_id": "r_nudge",
                    "text": "Write the test notes",
                    "mode": "agent",
                }
            )
            events = receive_until(ws, "run.finished")
            ws.receive_json()  # idle

    # Model was called 3 times: turn 1 (text promise), turn 2 (after nudge -> tool call), turn 3 (final text)
    assert len(provider.calls) == 3
    assert any(e["type"] == "step.started" and e["tool"] == "recall" for e in events)
    assert events[-1]["ok"] is True
