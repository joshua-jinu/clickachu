"""Automatic personal memory uses only user statements and no extra inference calls."""

import pytest
from fastapi.testclient import TestClient

from clickachu.api.protocol import MemoryCreate, MemoryUpdate, SettingsUpdate
from clickachu.context.selection import FocusedElement
from clickachu.db.conn import Database
from clickachu.llm.provider import Done, TextDelta
from clickachu.memory.automatic import personal_facts
from clickachu.memory.service import MemoryService
from clickachu.memory.store import MemoryStore
from clickachu.settings import SettingsStore
from tests.conftest import AUTH, BASE_URL, TOKEN, WS_URL
from tests.test_ws import receive_until


@pytest.fixture
def memory(db, settings_store):
    return MemoryService(MemoryStore(db), settings_store)


def test_durable_facts_persist_and_are_recalled_without_recent_fill(memory, db):
    outcomes = memory.observe_user_message(
        "My name is Nidhish. I live in Bengaluru. I prefer concise answers."
    )
    assert len(outcomes) == 3
    db.close()
    reopened_db = Database(db.path)
    try:
        reopened = MemoryService(MemoryStore(reopened_db), SettingsStore(reopened_db))
        assert len(reopened.store.list()) == 3
        recall = reopened.recall("How should I install dependencies?", limit=3, fill_recent=False)
        assert any("concise answers" in m.content for m in recall)
        personal = reopened.recall("What do you know about me?", limit=8, fill_recent=False)
        assert len(personal) == 3
    finally:
        reopened_db.close()


def test_duplicate_correction_undo_and_disabled_records(memory):
    old = memory.observe_user_message("I live in Pune.")[0].memory
    assert memory.observe_user_message("I live in pune!") == []
    replacement = memory.observe_user_message("I live in Bengaluru.")[0]
    assert replacement.action == "superseded"
    assert [m.id for m in memory.store.list()] == [replacement.memory.id]
    memory.store.delete(replacement.memory.id)
    assert [m.id for m in memory.store.list()] == [old.id]
    memory.store.update(old.id, MemoryUpdate(enabled=False))
    assert memory.observe_user_message("I live in Pune.") == []
    assert not memory.store.get(old.id).enabled


def test_unrelated_preferences_coexist_and_clear_response_preference_updates(memory):
    memory.observe_user_message("I prefer pnpm over npm.")
    memory.observe_user_message("I like jazz.")
    memory.observe_user_message("I prefer brief responses.")
    updated = memory.observe_user_message("I prefer detailed responses.")[0]
    assert updated.action == "superseded"
    assert len(memory.store.list()) == 3
    assert all("brief" not in m.content for m in memory.store.list())


@pytest.mark.parametrize(
    "text",
    [
        "Open Spotify and play music",
        "I prefer tea today.",
        "I would prefer short answers.",
        "Summarise this:\nMy name is SomeoneElse. I live in Paris.",
        "> I live in Paris.",
        '"I live in Paris."',
        "```\nI prefer Python.\n```",
        "Don't remember this. My name is Secret.",
        "I prefer this.",
        "My name is Example if I am pretending.",
        "My name is Alice?",
        "I prefer password: supersecret",
        "I like sk-abc123DEF456ghi789JKL012mno345",
    ],
)
def test_commands_source_text_transient_details_and_secrets_are_not_saved(memory, text):
    assert memory.observe_user_message(text) == []
    assert memory.store.list() == []


def test_memory_off_blocks_automatic_save_and_recall(memory, settings_store):
    memory.observe_user_message("I prefer pnpm.")
    settings_store.update(SettingsUpdate(memory_enabled=False))
    assert memory.observe_user_message("My name is Alice.") == []
    assert memory.recall("pnpm") == []
    assert len(memory.store.list()) == 1


def test_automatic_save_emits_notice_and_uses_one_answer_call(make_app):
    app = make_app([TextDelta("Hello Nidhish."), Done()])
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json(
                {
                    "type": "run",
                    "run_id": "r",
                    "mode": "ask",
                    "text": "My name is Nidhish. I prefer concise answers.",
                }
            )
            events = receive_until(ws, "run.finished")
        assert len([e for e in events if e["type"] == "memory.saved"]) == 2
        assert len(app.state.fake_provider.calls) == 1
        assert "concise answers" in app.state.fake_provider.calls[0]["messages"][0]["content"]
        assert len(client.get("/memories").json()) == 2


def test_selected_documents_never_become_personal_memory(make_app, win32):
    win32.focus = FocusedElement("Edit", "My name is SomeoneElse. I prefer verbose replies.")
    app = make_app([TextDelta("A person introduces themself."), Done()])
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        context = client.post("/context/capture").json()["id"]
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r", "text": "summarise", "context_id": context})
            events = receive_until(ws, "run.finished")
        assert not any(e["type"] == "memory.saved" for e in events)
        assert client.get("/memories").json() == []


def test_existing_explicit_preferences_are_in_small_local_recall(memory):
    note = memory.store.create(
        MemoryCreate(content="Use pnpm for installing dependencies.", category="instruction")
    )
    assert note.id in [
        m.id for m in memory.recall("Set up the project", limit=3, fill_recent=False)
    ]


def test_package_name_with_dot_is_preserved():
    assert personal_facts("I prefer Node.js.")[0].data.content == "User prefers Node.js."


def test_informal_fact_and_old_profile_recall(memory):
    saved = memory.observe_user_message("I'm based in Bengaluru.")[0].memory
    for i in range(60):
        memory.store.create(
            MemoryCreate(content=f"Project note {i}", scope_type="project", scope_key="demo")
        )
    for i in range(4):
        memory.observe_user_message(f"I like music genre {i}.")
    memories = memory.recall("Where do I live?", limit=3, fill_recent=False)
    assert memories[0].id == saved.id


def test_pinned_fact_correction_keeps_pin_and_replaces_stale_value(memory):
    old = memory.observe_user_message("My name is Alice.")[0].memory
    memory.store.update(old.id, MemoryUpdate(pinned=True))
    new = memory.observe_user_message("My name is Bob.")[0].memory
    assert new.pinned
    assert [m.id for m in memory.store.list()] == [new.id]


def test_personal_recall_skips_public_research_router(make_app):
    app = make_app([TextDelta("You live in Bengaluru."), Done()])
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        client.put("/settings", json={"auto_web_lookup": True})
        client.post("/memories", json={"content": "User lives in Bengaluru."})
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r", "text": "Where do I live?"})
            events = receive_until(ws, "run.finished")
        assert len(app.state.fake_provider.calls) == 1
        assert not any(e["type"] == "step.started" for e in events)
        assert "Bengaluru" in app.state.fake_provider.calls[0]["messages"][0]["content"]


def test_rewriting_first_person_prose_does_not_save_it(make_app):
    app = make_app([TextDelta("Rewritten text."), Done()])
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json(
                {
                    "type": "run",
                    "run_id": "r",
                    "action": "rewrite",
                    "text": "My name is Fictional. I live in Paris.",
                }
            )
            events = receive_until(ws, "run.finished")
        assert not any(e["type"] == "memory.saved" for e in events)
        assert client.get("/memories").json() == []
