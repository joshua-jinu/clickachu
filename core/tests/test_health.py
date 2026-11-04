import httpx

from tests.conftest import AUTH, BASE_URL, AppFactory


async def test_health(make_app: AppFactory) -> None:
    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.get("/health", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "0.1.0"}
