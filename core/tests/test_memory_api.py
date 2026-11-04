"""Memory HTTP routes and memory in runs: remember → toast event → recall → memory off (§14)."""

from typing import Any

from fastapi.testclient import TestClient

from clickachu.llm.provider import Done, TextDelta
from tests.conftest import AUTH, BASE_URL, TOKEN, WS_URL, AppFactory
from tests.fakes import QueueProvider
from tests.test_ws import receive_until

KEY = "sk-abc123DEF456ghi789JKL012mno345"


def client(make_app: AppFactory) -> TestClient:
    return TestClient(make_app(), base_url=BASE_URL, headers=AUTH)


def test_memory_routes_require_token(make_app: AppFactory) -> None:
    with TestClient(make_app(), base_url=BASE_URL) as c:
        assert c.get("/memories").status_code == 401


def test_crud_search_and_filters(make_app: AppFactory) -> None:
    with client(make_app) as c:
        a = c.post("/memories", json={"content": "I prefer pnpm over npm"}).json()
        b = c.post(
            "/memories",
            json={
                "content": "TeamFuse deploys on Fridays",
                "scope_type": "project",
                "scope_key": "TeamFuse",
            },
        ).json()
        assert c.post("/memories", json={"content": "x", "scope_type": "app"}).status_code == 422

        assert {m["id"] for m in c.get("/memories").json()} == {a["id"], b["id"]}
        assert [m["id"] for m in c.get("/memories", params={"scope_type": "project"}).json()] == [
            b["id"]
        ]
        assert [m["id"] for m in c.get("/memories", params={"q": "pnpm"}).json()] == [a["id"]]
        assert c.get("/memories", params={"q": "pnpm", "scope_type": "project"}).json() == []

        patched = c.patch(f"/memories/{a['id']}", json={"pinned": True, "enabled": False}).json()
        assert patched["pinned"] and not patched["enabled"]
        assert c.patch("/memories/999", json={"pinned": True}).status_code == 404

        assert c.delete(f"/memories/{a['id']}").status_code == 204
        assert c.delete(f"/memories/{a['id']}").status_code == 404
        assert [m["id"] for m in c.get("/memories").json()] == [b["id"]]


def test_secrets_refused_with_422(make_app: AppFactory) -> None:
    with client(make_app) as c:
        res = c.post("/memories", json={"content": f"my key is {KEY}"})
        assert res.status_code == 422 and "secret" in res.json()["detail"]
        ok = c.post("/memories", json={"content": "fine"}).json()
        assert c.patch(f"/memories/{ok['id']}", json={"content": f"key {KEY}"}).status_code == 422
        assert [m["content"] for m in c.get("/memories").json()] == ["fine"]


def test_export_import(make_app: AppFactory) -> None:
    with client(make_app) as c:
        c.post("/memories", json={"content": "one", "pinned": True})
        exported = c.get("/memories/export").json()
        assert exported["version"] == 1 and exported["memories"][0]["content"] == "one"
        assert c.post("/memories/import", json=exported).json() == {"imported": 0, "skipped": 1}
        c.delete(f"/memories/{c.get('/memories').json()[0]['id']}")
        assert c.post("/memories/import", json=exported).json() == {"imported": 1, "skipped": 0}
        assert c.get("/memories").json()[0]["pinned"] is True


def test_memory_off_blocks_writes_but_not_management(make_app: AppFactory) -> None:
    with client(make_app) as c:
        kept = c.post("/memories", json={"content": "kept"}).json()
        c.put("/settings", json={"memory_enabled": False})
        assert c.post("/memories", json={"content": "new"}).status_code == 409
        assert c.post("/memories/import", json={"version": 1, "memories": []}).status_code == 409
        assert [m["id"] for m in c.get("/memories").json()] == [kept["id"]]
        assert c.delete(f"/memories/{kept['id']}").status_code == 204


def extraction(content: str, **extra: str) -> list[Any]:
    body = {"content": content, "category": "preference", "scope": "global", **extra}
    import json

    return [TextDelta(json.dumps(body)), Done()]


def run(ws: Any, run_id: str, text: str, **kw: Any) -> list[dict[str, Any]]:
    ws.send_json({"type": "run", "run_id": run_id, "text": text, "mode": "ask", **kw})
    return receive_until(ws, "run.finished")


def system_prompt(provider: QueueProvider, call: int = -1) -> str:
    return provider.calls[call]["messages"][0]["content"]


def test_remember_then_recall_then_memory_off(make_app: AppFactory) -> None:
    provider = QueueProvider(
        [
            extraction("Prefers pnpm over npm."),  # run 1: extraction
            [TextDelta("Noted."), Done()],  # run 1: answer
            [TextDelta("Use pnpm."), Done()],  # run 2: answer
            [TextDelta("Use whatever."), Done()],  # run 3 (memory off)
            [TextDelta("ok"), Done()],  # run 4 (memory off, remember)
        ]
    )
    app = make_app(provider=provider)
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as c:
        with c.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})

            first = run(ws, "r1", "Remember that I prefer pnpm over npm")
            saved = [m for m in first if m["type"] == "memory.saved"]
            assert len(saved) == 1
            assert saved[0]["memory"]["content"] == "Prefers pnpm over npm."
            assert saved[0]["memory"]["scope"] == "global"
            assert first[-1]["text"] == "Noted."
            # The extraction call saw only the user's message.
            assert (
                "Remember that I prefer pnpm over npm"
                in provider.calls[0]["messages"][1]["content"]
            )
            # The answer's prompt already carries the new memory and the acknowledgement note.
            assert "Prefers pnpm over npm." in system_prompt(provider)
            assert "just asked you to remember" in system_prompt(provider)

            stored = c.get("/memories").json()
            assert [m["content"] for m in stored] == ["Prefers pnpm over npm."]
            undo_id = saved[0]["memory"]["id"]
            assert undo_id == stored[0]["id"]

            run(ws, "r2", "How should I install deps?")
            prompt = system_prompt(provider)
            assert "Known about the user" in prompt and "Prefers pnpm over npm." in prompt
            assert c.get("/memories").json()[0]["use_count"] >= 1

            c.put("/settings", json={"memory_enabled": False})
            calls_before = len(provider.calls)
            run(ws, "r3", "How should I install deps?")
            assert "Known about the user" not in system_prompt(provider)
            assert "pnpm" not in system_prompt(provider)

            off = run(ws, "r4", "Remember that I like tabs")
            assert not [m for m in off if m["type"] == "memory.saved"]
            assert len(provider.calls) == calls_before + 2  # answers only, no extraction call
            assert len(c.get("/memories").json()) == 1

            c.put("/settings", json={"memory_enabled": True})
            assert c.delete(f"/memories/{undo_id}").status_code == 204


def test_no_memory_block_when_store_is_empty(make_app: AppFactory) -> None:
    app = make_app([TextDelta("hi"), Done()])
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as c:
        with c.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            run(ws, "r1", "hello")
    assert "Known about the user" not in system_prompt(app.state.fake_provider)


def test_secret_in_remember_is_not_saved(make_app: AppFactory) -> None:
    provider = QueueProvider([extraction(f"API key is {KEY}"), [TextDelta("ok"), Done()]])
    with TestClient(make_app(provider=provider), base_url=BASE_URL, headers=AUTH) as c:
        with c.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            events = run(ws, "r1", "Remember my api key")
        assert not [m for m in events if m["type"] == "memory.saved"]
        assert c.get("/memories").json() == []


def test_update_verdict_supersedes_and_undo_restores(make_app: AppFactory) -> None:
    provider = QueueProvider(
        [
            extraction("Prefers npm over yarn."),
            [TextDelta("ok"), Done()],
            extraction("Prefers pnpm over yarn."),
            [TextDelta('{"verdict": "update"}'), Done()],  # dedup judge
            [TextDelta("ok"), Done()],
        ]
    )
    with TestClient(make_app(provider=provider), base_url=BASE_URL, headers=AUTH) as c:
        with c.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            run(ws, "r1", "Remember I prefer npm over yarn")
            second = run(ws, "r2", "Remember I prefer pnpm over yarn")
        new = next(m for m in second if m["type"] == "memory.saved")["memory"]
        assert [m["content"] for m in c.get("/memories").json()] == ["Prefers pnpm over yarn."]

        c.delete(f"/memories/{new['id']}")  # Undo
        assert [m["content"] for m in c.get("/memories").json()] == ["Prefers npm over yarn."]
