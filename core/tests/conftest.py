from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI

from clickachu.api.http import AppConfig, create_app
from clickachu.api.protocol import SettingsUpdate
from clickachu.context.capture import ContextService
from clickachu.db.conn import Database
from clickachu.llm.provider import LLMEvent
from clickachu.projects.profiles import ProjectStore
from clickachu.settings import SettingsStore
from clickachu.tools.base import ToolWorkers
from clickachu.tools.registry import ToolRegistry
from tests.fake_win32 import FakeWin32
from tests.fakes import FakeProvider, FakeSecretStore, Hang, QueueProvider

PORT = 8765
TOKEN = "t" * 64
BASE_URL = f"http://127.0.0.1:{PORT}"
WS_URL = f"ws://127.0.0.1:{PORT}/ws"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def no_unexpected_search_network(monkeypatch):
    """Unit tests must never silently query public search engines."""
    from ddgs import DDGS

    def disabled(*args, **kwargs):
        raise RuntimeError("Public search transport must be stubbed in unit tests")

    monkeypatch.setattr(DDGS, "text", disabled)


AppFactory = Callable[..., FastAPI]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "clickachu.db")
    yield database
    database.close()


@pytest.fixture
def settings_store(db: Database) -> SettingsStore:
    store = SettingsStore(db)
    store.update(
        # Unit tests opt into web lookup explicitly with a fake search tool, never real network.
        SettingsUpdate(
            ask_model="test/ask-model", agent_model="test/agent-model", auto_web_lookup=False
        )
    )
    return store


async def run_inline(fn: Callable[..., object], *args: object, timeout: float = 0) -> object:
    """Stands in for `run_com` in tests: no COM thread, call directly."""
    del timeout
    return fn(*args)


@pytest.fixture
def win32() -> FakeWin32:
    return FakeWin32()


@pytest.fixture
def context_service(win32: FakeWin32, db: Database) -> ContextService:
    return ContextService(
        api=win32, run_com=run_inline, restore_delay_s=0, projects=ProjectStore(db)
    )


@pytest.fixture
def make_app(settings_store: SettingsStore, context_service: ContextService) -> AppFactory:
    def factory(
        script: Sequence[LLMEvent | Hang] = (),
        *,
        provider: FakeProvider | QueueProvider | None = None,
        secrets: FakeSecretStore | None = None,
        ws_auth_timeout_s: float = 2.0,
        registry: ToolRegistry | None = None,
        workers: ToolWorkers | None = None,
        approval_timeout_s: float = 300.0,
    ) -> FastAPI:
        provider = provider or FakeProvider(script)
        app = create_app(
            AppConfig(port=PORT, token=TOKEN, ws_auth_timeout_s=ws_auth_timeout_s),
            settings=settings_store,
            secrets=secrets or FakeSecretStore(),
            provider_factory=provider.factory,
            context_service=context_service,
            registry=registry,
            workers=workers,
            approval_timeout_s=approval_timeout_s,
        )
        app.state.fake_provider = provider
        return app

    return factory
