import io
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from urllib.error import URLError

import httpx
import pytest

from clickachu.api.http import openrouter_factory
from clickachu.api.protocol import Settings
from clickachu.llm import ollama_connection
from clickachu.llm.ollama_connection import is_local_endpoint, normalize_endpoint
from clickachu.llm.provider import Done, TextDelta
from tests.conftest import AUTH, BASE_URL


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.72.23.238", "http://10.72.23.238:11434/v1"),
        ("mac.local:11435", "http://mac.local:11435/v1"),
        ("http://localhost:11434/", "http://localhost:11434/v1"),
        ("https://my-server.example/prefix/v1/", "https://my-server.example/prefix/v1"),
        ("http://[::1]:11434", "http://[::1]:11434/v1"),
    ],
)
def test_endpoint_normalization(value, expected):
    assert normalize_endpoint(value) == expected


@pytest.mark.parametrize(
    "value", ["", "http://", "file:///etc", "http://user:secret@host", "http://x?q=1"]
)
def test_invalid_endpoints(value):
    with pytest.raises(ValueError):
        normalize_endpoint(value)


def test_local_detection_does_not_confuse_remote_hosts():
    assert is_local_endpoint("http://127.0.0.1:11434/v1")
    assert is_local_endpoint("http://[::1]:11434/v1")
    assert not is_local_endpoint("http://localhost.example:11434/v1")
    assert not is_local_endpoint("http://10.72.23.238:11434/v1")


async def test_draft_connection_uses_exact_remote_host_without_saving(make_app, monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        response = io.BytesIO(
            json.dumps({"models": [{"name": "remote-qwen", "size": 42}]}).encode()
        )
        response.status = 200
        return response

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app()), base_url=BASE_URL
    ) as c:
        res = await c.get("/ollama/status", params={"base_url": "10.72.23.238"}, headers=AUTH)
        assert res.json()["models"][0]["name"] == "remote-qwen"
        assert calls == ["http://10.72.23.238:11434/api/tags"]
        saved = (await c.get("/settings", headers=AUTH)).json()
        assert saved["ollama_base_url"] == "http://127.0.0.1:11434/v1"

        def unavailable(*args, **kwargs):
            raise URLError("Host asleep")

        monkeypatch.setattr("urllib.request.urlopen", unavailable)
        res = await c.get("/ollama/status", params={"base_url": "10.72.23.238"}, headers=AUTH)
        assert res.json()["running"] is False
        assert res.json()["models"] == []  # Never substitute the local model list.
        assert (
            await c.get("/ollama/status", params={"base_url": "file:///etc"}, headers=AUTH)
        ).status_code == 422


async def test_switch_to_local_clears_overrides_and_retains_remote_profile(make_app, monkeypatch):
    clear_user = Mock()
    monkeypatch.setattr(ollama_connection, "_clear_user_host", clear_user)
    monkeypatch.setenv("OLLAMA_HOST", "http://10.72.23.238:11434")
    remote = {"base_url": "10.72.23.238", "model": "qwen3.5:9b", "cpu_only": False}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app()), base_url=BASE_URL
    ) as c:
        res = await c.put(
            "/settings",
            headers=AUTH,
            json={
                "provider": "ollama",
                "ollama_base_url": "10.72.23.238",
                "ollama_model": "qwen3.5:9b",
                "ollama_remote_profile": remote,
            },
        )
        assert res.status_code == 200
        clear_user.assert_not_called()
        res = await c.put(
            "/settings",
            headers=AUTH,
            json={
                "ollama_base_url": "http://127.0.0.1:11434/v1",
                "ollama_model": "local-model",
            },
        )
        assert res.status_code == 200
        assert "OLLAMA_HOST" not in os.environ
        clear_user.assert_called_once()
        saved = (await c.get("/settings", headers=AUTH)).json()
        assert saved["ollama_model"] == "local-model"
        assert saved["ollama_remote_profile"]["model"] == "qwen3.5:9b"
        assert saved["ollama_remote_profile"]["base_url"] == "http://10.72.23.238:11434/v1"


def test_cleanup_failure_is_reported_without_redirecting_local_requests(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://remote:11434")
    monkeypatch.setattr(ollama_connection, "_clear_user_host", Mock(side_effect=OSError("denied")))
    assert "denied" in ollama_connection.clear_client_host()
    assert "OLLAMA_HOST" not in os.environ


def test_custom_remote_port_is_recognized_as_ollama():
    provider = openrouter_factory(
        Settings(provider="ollama", ollama_base_url="http://mac.local:12345/v1"), ""
    )
    assert provider._is_ollama is True
    assert str(provider._client.base_url) == "http://mac.local:12345/v1/"
    assert provider._client.max_retries == 0


async def test_streaming_uses_selected_endpoint_even_with_conflicting_host_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://wrong-host:11434")
    requests = []

    def handle(request):
        requests.append(request)
        chunk = {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "qwen",
            "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    for endpoint in ["http://10.72.23.238:11434/v1", "http://127.0.0.1:11434/v1"]:
        provider = openrouter_factory(Settings(provider="ollama", ollama_base_url=endpoint), "")
        client = provider._client
        provider._client = client.with_options(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        )
        try:
            events = [e async for e in provider.stream("qwen", [{"role": "user", "content": "Hi"}])]
            assert isinstance(events[0], TextDelta) and events[0].text == "Hello"
            assert isinstance(events[-1], Done)
            assert str(requests[-1].url) == f"{endpoint}/chat/completions"
            assert json.loads(requests[-1].content)["model"] == "qwen"
        finally:
            await provider._client.close()
            provider._client = client


@pytest.mark.skipif(os.name != "nt", reason="Windows user environment cleanup")
def test_windows_cleanup_only_deletes_user_ollama_host(monkeypatch):
    import ctypes
    import sys

    key_context = MagicMock()
    registry = SimpleNamespace(
        HKEY_CURRENT_USER="user",
        KEY_SET_VALUE=2,
        OpenKey=Mock(return_value=key_context),
        DeleteValue=Mock(),
    )
    send = Mock()
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(SendMessageTimeoutW=send))
    )
    ollama_connection._clear_user_host()
    registry.OpenKey.assert_called_once_with("user", "Environment", 0, 2)
    registry.DeleteValue.assert_called_once_with(key_context.__enter__.return_value, "OLLAMA_HOST")
    send.assert_called_once()
