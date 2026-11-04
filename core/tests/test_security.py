"""Auth middleware: token, Host and Origin checks (PLAN §6.6–6.7)."""

import httpx
import pytest

from tests.conftest import AUTH, BASE_URL, PORT, TOKEN, AppFactory


async def get(app_factory: AppFactory, path: str, headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app_factory())
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return await client.get(path, headers=headers)


async def test_missing_token_is_401(make_app: AppFactory) -> None:
    response = await get(make_app, "/health", {})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "auth", [f"Bearer {'x' * 64}", f"Bearer {TOKEN}x", TOKEN, f"Basic {TOKEN}", "Bearer "]
)
async def test_wrong_token_is_401(make_app: AppFactory, auth: str) -> None:
    response = await get(make_app, "/health", {"Authorization": auth})
    assert response.status_code == 401


@pytest.mark.parametrize("host", ["localhost:8765", f"127.0.0.1:{PORT + 1}", "evil.example"])
async def test_bad_host_is_403(make_app: AppFactory, host: str) -> None:
    response = await get(make_app, "/health", {**AUTH, "Host": host})
    assert response.status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost:5174", "null"])
async def test_bad_origin_is_403(make_app: AppFactory, origin: str) -> None:
    response = await get(make_app, "/health", {**AUTH, "Origin": origin})
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", ["http://localhost:5173", "file://", "app://clickachu"])
async def test_good_origin_gets_cors(make_app: AppFactory, origin: str) -> None:
    response = await get(make_app, "/health", {**AUTH, "Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


async def test_good_request_without_origin(make_app: AppFactory) -> None:
    response = await get(make_app, "/health", AUTH)
    assert response.status_code == 200


async def test_preflight_needs_no_token(make_app: AppFactory) -> None:
    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.options(
            "/settings",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
    assert response.status_code == 204
    assert "authorization" in response.headers["access-control-allow-headers"]


async def test_settings_roundtrip_and_secret_is_write_only(make_app: AppFactory) -> None:
    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        put = await client.put("/settings", headers=AUTH, json={"agent_model": "x/agent"})
        assert put.status_code == 200
        assert put.json()["agent_model"] == "x/agent"
        assert put.json()["ask_model"] == "test/ask-model"

        secret = await client.put("/secrets/openrouter", headers=AUTH, json={"key": "sk-new"})
        assert secret.status_code == 204

        got = (await client.get("/settings", headers=AUTH)).json()
    assert got["has_openrouter_key"] is True
    assert "sk-new" not in str(got)


async def test_settings_rejects_non_http_base_url(make_app: AppFactory) -> None:
    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        put = await client.put("/settings", headers=AUTH, json={"base_url": "file:///etc"})
    assert put.status_code == 422


async def test_ollama_status_and_settings_roundtrip(make_app: AppFactory) -> None:
    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        status_res = await client.get("/ollama/status", headers=AUTH)
        assert status_res.status_code == 200
        assert "running" in status_res.json()

        put = await client.put(
            "/settings",
            headers=AUTH,
            json={"provider": "ollama", "ollama_model": "qwen2.5:3b"},
        )
        assert put.status_code == 200
        assert put.json()["provider"] == "ollama"
        assert put.json()["ollama_model"] == "qwen2.5:3b"

        got = (await client.get("/settings", headers=AUTH)).json()
        assert got["provider"] == "ollama"
        assert got["ollama_model"] == "qwen2.5:3b"
