"""Behavioral regressions: bounded editor execution, web routing, and reasoning events."""

import asyncio
from types import SimpleNamespace

import pytest

from clickachu.agent.orchestrator import Orchestrator
from clickachu.api.protocol import ApproveMessage, RunMessage, SettingsUpdate
from clickachu.llm.provider import Done, ReasoningDelta, TextDelta
from clickachu.llm.router import heuristic_mode, notepad_write_requested
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult
from clickachu.tools.notepad import WriteNotepadArgs, WriteNotepadTool
from clickachu.tools.registry import ToolRegistry, create_default_registry
from tests.fakes import FakeProvider, FakeSecretStore


def test_provider_preserves_separate_reasoning_deltas():
    from clickachu.llm.openrouter import OpenRouterProvider

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def __aiter__(self):
            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            reasoning="Inspect the controls.",
                            content=None,
                            tool_calls=[],
                        )
                    )
                ],
            )
            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="Done.",
                            tool_calls=[],
                        )
                    )
                ],
            )

    async def create(**kwargs):
        return Stream()

    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._is_openrouter = False
    provider._is_gemini = False
    provider._is_ollama = False
    provider._reasoning_effort = "auto"
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )
    )

    async def collect():
        return [event async for event in provider.stream("test", [])]

    events = asyncio.run(collect())
    assert events[0] == ReasoningDelta("Inspect the controls.")
    assert events[1] == TextDelta("Done.")


def test_browser_snapshot_contains_article_text_and_control_refs():
    from clickachu.automation.browser_worker import TAG_SCRIPT, BrowserWorker

    class Page:
        url = "https://example.test/company"

        async def title(self):
            return "Company"

        async def evaluate(self, script):
            if script == TAG_SCRIPT:
                return [{"ref": "e1", "role": "link", "name": "About", "href": "/about"}]
            return "A paragraph describing the company and its services."

    worker = BrowserWorker()

    async def get_page(run_id=None):
        return Page()

    worker.get_page = get_page
    text, refs = asyncio.run(worker.read_snapshot("run"))
    assert "paragraph describing the company" in text
    assert "e1 [link]" in text
    assert "https://example.test/company" in text
    assert "e1" in refs


@pytest.mark.parametrize(
    "text",
    [
        "you can checkonline and tell me",
        "check online",
        "look it up",
        "can you search online for Kalvium?",
        "verify this on the internet",
    ],
)
def test_lookup_routes_and_exposes_browser_tools(text):
    assert heuristic_mode(text) == "agent"
    names = {s["function"]["name"] for s in create_default_registry().scoped_schemas(text)}
    assert {"web_search", "web_fetch"} <= names
    assert "write_file" not in names


@pytest.mark.parametrize("text", ["don't search online", "do not do anything just tell me"])
def test_negative_requests_do_not_start_actions(text):
    assert heuristic_mode(text) == "ask"


def test_notepad_request_is_not_a_file_save():
    prompt = "can you open notepad and write me a description about porsche"
    assert notepad_write_requested(prompt)
    schemas = create_default_registry().scoped_schemas(prompt)
    assert [s["function"]["name"] for s in schemas] == ["write_notepad"]
    assert not notepad_write_requested("open notepad and write a poem then save the file")


@pytest.mark.parametrize("decision", ["approve", "deny"])
@pytest.mark.parametrize(
    "user_request",
    [
        "open notepad and write a description of Porsche",
        "Open nodepad and write me a cookie recipe",
    ],
)
def test_notepad_workflow_generates_once_and_honors_approval(
    settings_store, decision, user_request
):
    settings_store.update(SettingsUpdate(provider="ollama", ollama_model="qwen3:4b-instruct"))

    class FakeNotepad(Tool):
        name = "write_notepad"
        description = "Write in a new unsaved document"
        Args = WriteNotepadArgs
        tier = Tier.CONFIRM

        def __init__(self):
            self.written = []

        def run(self, args, ctx):
            self.written.append(args.text)
            return ToolResult(ok=True, summary="Wrote text in a new unsaved Notepad document.")

        def verify(self, args, result):
            return True

    provider = FakeProvider(
        [
            ReasoningDelta("Compose a short description."),
            TextDelta("Porsche makes sports cars."),
            Done(),
        ]
    )
    tool = FakeNotepad()
    registry = ToolRegistry()
    registry.register(tool)
    events = []
    runner = None

    async def emit(event):
        events.append(event)
        if event.type == "approval.request":
            # Approval future is registered just after the event is emitted.
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.create_task(
                    runner.handle_approve(
                        ApproveMessage(
                            run_id=event.run_id,
                            step_id=event.step_id,
                            decision=decision,
                        )
                    )
                )
            )

    runner = Orchestrator(
        emit=emit,
        settings=settings_store,
        secrets=FakeSecretStore(),
        provider_factory=provider.factory,
        registry=registry,
        approval_timeout_s=1,
    )
    runner._start_conversation("notepad", None, "what can you do?")
    runner._record_message("notepad", "assistant", "A long unrelated capabilities list.")
    asyncio.run(
        runner.handle_run(
            RunMessage(
                run_id="notepad",
                text=user_request,
                mode="agent",
            )
        )
    )
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] is None
    assert len(provider.calls[0]["messages"]) == 2
    assert tool.written == (["Porsche makes sports cars."] if decision == "approve" else [])
    assert any(e.type == "run.detail" and e.kind == "plan" for e in events)
    assert any(e.type == "run.detail" and e.kind == "reasoning" for e in events)
    assert not any(e.type == "token" and "Porsche makes" in e.delta for e in events)
    final = next(e for e in events if e.type == "run.finished")
    assert final.ok == (decision == "approve")


@pytest.mark.parametrize("fresh", [True, False])
def test_notepad_tool_never_clears_existing_text(db, monkeypatch, fresh):
    import clickachu.tools.notepad as module

    class Editor:
        value = "Existing work"

        def set_focus(self):
            pass

        def has_keyboard_focus(self):
            return True

    editor = Editor()

    class API:
        clipboard = "original clipboard"
        seq = 0
        chords = []

        def focus_window(self, hwnd):
            pass

        def foreground_window(self):
            return 42

        def window_pid(self, hwnd):
            return 1

        def process_name(self, pid):
            return "Notepad.exe"

        def send_chord(self, *keys):
            self.chords.append(keys)
            if keys == (0x11, 0x4E) and fresh:
                editor.value = ""
            if keys == (0x11, 0x56):
                editor.value = self.clipboard

        def get_clipboard_text(self):
            return self.clipboard

        def set_clipboard_text(self, value):
            self.clipboard = value
            self.seq += 1

        def clipboard_sequence(self):
            return self.seq

    api = API()
    tick = iter(range(100))
    monkeypatch.setattr(module, "RealWin32", lambda: api)
    monkeypatch.setattr(module, "wait_for_modifier_release", lambda api: True)
    monkeypatch.setattr(module, "_notepad_editor", lambda hwnd: editor)
    monkeypatch.setattr(module, "_editor_value", lambda e: e.value)
    monkeypatch.setattr(
        module.OpenAppTool,
        "run",
        lambda *args: ToolResult(
            ok=True,
            data={"hwnd": 42},
            summary="Opened Notepad",
        ),
    )
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(tick))
    ctx = ToolContext(run_id="r", step_id="s", snapshot=None, db=db)
    result = WriteNotepadTool().run(WriteNotepadArgs(text="New document"), ctx)
    assert result.ok == fresh
    assert editor.value == ("New document" if fresh else "Existing work")
    assert (0x11, 0x41) not in api.chords  # Ctrl+A must never be used to erase an old document
    assert api.clipboard == "original clipboard"
