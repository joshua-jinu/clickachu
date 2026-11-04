"""WebSocket auth, Ask-mode event sequence and cancel (PLAN §6.6, §7, §12)."""

import time
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from clickachu.api.protocol import Usage
from clickachu.api.ws import AUTH_FAILED_CODE
from clickachu.llm.provider import Done, TextDelta
from clickachu.settings import SettingsStore
from tests.conftest import BASE_URL, TOKEN, WS_URL, AppFactory
from tests.fakes import FakeProvider, FakeSecretStore, Hang


def receive_until(ws: WebSocketTestSession, msg_type: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == msg_type:
            return messages


def test_ws_auth_timeout_closes_4401(make_app: AppFactory) -> None:
    with TestClient(make_app(ws_auth_timeout_s=0.1), base_url=BASE_URL) as client:
        with client.websocket_connect(WS_URL) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
    assert exc.value.code == AUTH_FAILED_CODE


@pytest.mark.parametrize(
    "first",
    [
        {"type": "auth", "token": "wrong"},
        {"type": "auth"},
        {"type": "run", "run_id": "r1", "text": "hi"},
    ],
)
def test_ws_bad_auth_closes_4401(make_app: AppFactory, first: dict[str, Any]) -> None:
    with TestClient(make_app(), base_url=BASE_URL) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json(first)
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
    assert exc.value.code == AUTH_FAILED_CODE


def test_ws_bad_origin_refused(make_app: AppFactory) -> None:
    with TestClient(make_app(), base_url=BASE_URL) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(WS_URL, headers={"Origin": "https://evil.example"}):
                pass


def test_ask_event_sequence(make_app: AppFactory) -> None:
    usage = Usage(input_tokens=12, output_tokens=3, cost_usd=0.0001)
    app = make_app([TextDelta("Hel"), TextDelta("lo"), Done(usage)])
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r1", "text": "Say hello", "mode": "ask"})
            messages = receive_until(ws, "run.finished")
            messages.append(ws.receive_json())

    assert messages == [
        {"type": "run.started", "run_id": "r1", "mode": "ask"},
        {"type": "orb.state", "state": "thinking"},
        {"type": "token", "run_id": "r1", "delta": "Hel"},
        {"type": "token", "run_id": "r1", "delta": "lo"},
        {
            "type": "run.finished",
            "run_id": "r1",
            "ok": True,
            "text": "Hello",
            "usage": usage.model_dump(),
        },
        {"type": "orb.state", "state": "idle"},
    ]
    call = app.state.fake_provider.calls[0]
    assert call["model"] == "test/ask-model"
    assert call["tools"] is None
    assert call["messages"][0]["role"] == "system"
    assert "untrusted_data" in call["messages"][0]["content"]
    assert call["messages"][-1] == {"role": "user", "content": "Say hello"}


def test_action_adds_chip_instruction(make_app: AppFactory) -> None:
    app = make_app([Done()])
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r1", "text": "text", "action": "summarise"})
            receive_until(ws, "run.finished")
    assert "TL;DR" in app.state.fake_provider.calls[0]["messages"][0]["content"]


def test_cancel_stops_run_quickly(make_app: AppFactory) -> None:
    provider = FakeProvider([TextDelta("partial"), Hang(), TextDelta("never")])
    app = make_app(provider=provider)
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r1", "text": "long", "mode": "ask"})
            receive_until(ws, "token")
            started = time.monotonic()
            ws.send_json({"type": "cancel", "run_id": "r1"})
            finished = receive_until(ws, "run.finished")[-1]
            elapsed = time.monotonic() - started
            idle = ws.receive_json()

    assert elapsed < 0.5
    assert finished["ok"] is False
    assert finished["text"] == "partial"
    assert idle == {"type": "orb.state", "state": "idle"}
    assert provider.cancelled


def test_missing_key_is_run_error(make_app: AppFactory) -> None:
    app = make_app([Done()], secrets=FakeSecretStore(key=None))
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r1", "text": "hi"})
            error = ws.receive_json()
    assert error["type"] == "run.error"
    assert "API key" in error["message"]
    assert app.state.fake_provider.calls == []


def test_ollama_provider_does_not_require_gemini_key(
    make_app: AppFactory, settings_store: SettingsStore
) -> None:
    from clickachu.api.protocol import SettingsUpdate

    settings_store.update(SettingsUpdate(provider="ollama"))
    app = make_app([TextDelta("local answer"), Done()], secrets=FakeSecretStore(key=None))
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect(WS_URL) as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.send_json({"type": "run", "run_id": "r1", "text": "hi"})
            finished = receive_until(ws, "run.finished")[-1]
            assert finished["ok"] is True
            assert finished["text"] == "local answer"


def test_broadcast_reaches_all_windows(make_app: AppFactory) -> None:
    app = make_app([TextDelta("x"), Done()])
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect(WS_URL) as a, client.websocket_connect(WS_URL) as b:
            a.send_json({"type": "auth", "token": TOKEN})
            b.send_json({"type": "auth", "token": TOKEN})
            # Make sure b's auth has been processed before a starts the run.
            b.send_json({"type": "cancel", "run_id": "noop"})
            time.sleep(0.05)
            a.send_json({"type": "run", "run_id": "r1", "text": "hi"})
            assert receive_until(a, "run.finished")[-1]["text"] == "x"
            assert receive_until(b, "run.finished")[-1]["text"] == "x"
