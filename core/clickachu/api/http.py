"""HTTP API (PLAN §7) and app assembly."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from keyring.errors import KeyringError

from clickachu import __version__
from clickachu.agent.orchestrator import Orchestrator, ProviderFactory
from clickachu.api.protocol import (
    AudioDevice,
    ContextSnapshot,
    HealthResponse,
    OllamaModelInfo,
    OllamaStatusResponse,
    ReplaceRequest,
    ReplaceResponse,
    SecretUpdate,
    Settings,
    SettingsUpdate,
    SettingsView,
    VoiceStatusResponse,
    VoiceStopResponse,
)
from clickachu.api.routes import (
    create_conversations_router,
    create_log_router,
    create_memory_router,
    create_projects_router,
)
from clickachu.api.security import GuardMiddleware, default_origins
from clickachu.api.ws import Hub, create_ws_router
from clickachu.automation.browser_worker import (
    get_browser_worker,
    run_browser,
    shutdown_browser_worker,
)
from clickachu.automation.com_worker import run_com, shutdown_com_worker
from clickachu.config import get_paths
from clickachu.context.capture import ContextService, ContextStore
from clickachu.llm.provider import LLMProvider
from clickachu.memory.service import MemoryService
from clickachu.memory.store import MemoryStore
from clickachu.projects.profiles import ProjectStore
from clickachu.projects.roots import RootStore
from clickachu.secret_store import KeyringSecretStore, SecretStore
from clickachu.settings import SettingsStore
from clickachu.tools.base import ToolWorkers
from clickachu.tools.registry import ToolRegistry
from clickachu.voice.stt import VoiceService
from clickachu.watchers.filters import SuggestionManager
from clickachu.watchers.fullscreen import FullscreenWatcher
from clickachu.watchers.process_manager import get_process_manager, shutdown_process_manager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppConfig:
    port: int
    # None only with --dev-insecure.
    token: str | None
    allowed_origins: frozenset[str] = field(default_factory=default_origins)
    ws_auth_timeout_s: float = 2.0


_provider_cache: dict[tuple[str, str], LLMProvider] = {}


def openrouter_factory(settings: Settings, api_key: str) -> LLMProvider:
    # Imported lazily: `openai` adds ~2 s to sidecar startup and isn't needed until a run.
    from clickachu.llm.openrouter import OpenRouterProvider

    base_url = settings.active_base_url
    key = api_key if settings.provider == "gemini" else (api_key or "ollama")
    cache_key = (base_url, key)
    cached = _provider_cache.get(cache_key)
    if cached is not None:
        return cached
    prov = OpenRouterProvider(base_url=base_url, api_key=key)
    _provider_cache[cache_key] = prov
    return prov


def default_context_service(projects: ProjectStore) -> ContextService:
    # Imported lazily: pulls in comtypes/pywin32, which tests and non-Windows tooling don't need.
    from clickachu.automation.winapi import RealWin32

    return ContextService(api=RealWin32(), run_com=run_com, store=ContextStore(), projects=projects)


def create_app(
    config: AppConfig,
    *,
    settings: SettingsStore,
    secrets: SecretStore | None = None,
    provider_factory: ProviderFactory = openrouter_factory,
    context_service: ContextService | None = None,
    registry: ToolRegistry | None = None,
    workers: ToolWorkers | None = None,
    approval_timeout_s: float = 300.0,
    voice_service: VoiceService | None = None,
    fullscreen_watcher: FullscreenWatcher | None = None,
    suggestion_manager: SuggestionManager | None = None,
    auto_load_voice: bool = False,
) -> FastAPI:
    """Set `app.state.server` to the uvicorn.Server before it starts to get `READY <port>`."""
    secrets = secrets or KeyringSecretStore()
    db = settings.db
    project_store = ProjectStore(db)
    memory = MemoryService(MemoryStore(db), settings)
    context_service = context_service or default_context_service(project_store)
    hub = Hub(token=config.token, auth_timeout_s=config.ws_auth_timeout_s)
    orchestrator = Orchestrator(
        emit=hub.broadcast,
        settings=settings,
        secrets=secrets,
        provider_factory=provider_factory,
        contexts=context_service.store,
        memory=memory,
        registry=registry,
        workers=workers,
        approval_timeout_s=approval_timeout_s,
    )
    hub.on("run", orchestrator.handle_run)
    hub.on("approve", orchestrator.handle_approve)
    hub.on("cancel", orchestrator.handle_cancel)

    paths = get_paths()
    suggestion_mgr = suggestion_manager or SuggestionManager(
        db=db,
        context_store=context_service.store,
        project_store=project_store,
        settings_store=settings,
        process_manager=get_process_manager(),
        emit=hub.broadcast,
    )

    async def _handle_suggestion_respond(msg: dict[str, Any]) -> None:
        sug_id = msg.get("suggestion_id")
        decision = msg.get("decision")
        if sug_id and decision:
            suggestion_mgr.respond(sug_id, decision)

    hub.on("suggestion.respond", _handle_suggestion_respond)

    fs_watcher = fullscreen_watcher or FullscreenWatcher(emit=hub.broadcast)
    vc_service = voice_service or VoiceService(models_dir=paths.models, emit=hub.broadcast)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        server: uvicorn.Server | None = getattr(app.state, "server", None)
        announce = asyncio.create_task(_announce_ready(server, config.port)) if server else None
        suggestion_mgr.install()
        fs_watcher.start()
        try:
            vc_service.set_loop(asyncio.get_running_loop())
        except RuntimeError:
            pass
        if auto_load_voice:
            vc_service.load_model_async()
        yield
        if announce is not None:
            announce.cancel()
        suggestion_mgr.uninstall()
        fs_watcher.stop()
        vc_service.stop_recording()
        shutdown_com_worker()
        shutdown_browser_worker()
        shutdown_process_manager()

    app = FastAPI(title="Clickachu core", version=__version__, lifespan=lifespan)
    app.state.hub = hub
    app.state.orchestrator = orchestrator
    app.state.context_service = context_service
    app.state.memory = memory
    app.state.projects = project_store
    app.state.voice_service = vc_service
    app.state.suggestion_manager = suggestion_mgr
    app.state.fullscreen_watcher = fs_watcher

    @app.get("/health")
    async def health() -> HealthResponse:
        return HealthResponse(ok=True, version=__version__)

    @app.post("/context/capture")
    async def capture_context() -> ContextSnapshot:
        try:
            return await context_service.capture()
        except (TimeoutError, OSError) as exc:
            log.error("context capture failed: %s", exc)
            raise HTTPException(504, "context capture failed") from exc

    @app.get("/context/{context_id}")
    async def get_context(context_id: str) -> ContextSnapshot:
        snap = context_service.store.get(context_id)
        if snap is None:
            raise HTTPException(404, f"Context snapshot {context_id} not found")
        return snap

    @app.post("/context/replace")
    async def replace_context(body: ReplaceRequest) -> ReplaceResponse:
        return await context_service.replace(body.context_id, body.text, body.mode)

    @app.post("/browser/open")
    async def open_browser_window() -> dict[str, bool]:
        try:
            worker = get_browser_worker()
            s = settings.get()
            if s.browser_channel:
                worker.set_channel(s.browser_channel)
            await run_browser(worker.open_browser_window)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            log.error("failed to open browser window: %s", exc)
            raise HTTPException(500, f"Failed to open browser: {exc}") from exc

    @app.get("/voice/status")
    async def get_voice_status() -> VoiceStatusResponse:
        return VoiceStatusResponse(
            model_status=vc_service.model_status,
            recording=vc_service.is_recording,
        )

    @app.get("/voice/devices")
    async def get_voice_devices() -> list[AudioDevice]:
        devs = vc_service.get_input_devices()
        return [AudioDevice(**d) for d in devs]

    @app.post("/voice/start")
    async def start_voice(device: str | None = None) -> dict[str, bool]:
        s = settings.get()
        if not getattr(s, "voice_enabled", True):
            raise HTTPException(409, "voice is disabled in settings")
        use_device = device if device is not None else getattr(s, "voice_device", None)
        ok = vc_service.start_recording(device=use_device)
        if not ok:
            raise HTTPException(500, "failed to start voice recording")
        return {"ok": True}

    @app.post("/voice/stop")
    async def stop_voice() -> VoiceStopResponse:
        text = vc_service.stop_recording()
        return VoiceStopResponse(text=text)

    @app.get("/ollama/status")
    async def get_ollama_status() -> OllamaStatusResponse:
        s = settings.get()
        base = s.ollama_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        tags_url = f"{base}/api/tags"

        loop = asyncio.get_running_loop()

        def _check() -> OllamaStatusResponse:
            try:
                import json
                import urllib.request

                req = urllib.request.Request(tags_url, headers={"User-Agent": "Clickachu"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        raw_models = data.get("models", [])
                        models = [
                            OllamaModelInfo(
                                name=m.get("name", ""),
                                size=m.get("size", 0),
                                modified_at=m.get("modified_at"),
                            )
                            for m in raw_models
                            if m.get("name")
                        ]
                        return OllamaStatusResponse(running=True, models=models)
            except Exception as exc:  # noqa: BLE001
                return OllamaStatusResponse(running=False, models=[], error=str(exc))
            return OllamaStatusResponse(running=False, models=[], error="Unknown error")

        return await loop.run_in_executor(None, _check)

    @app.get("/settings")
    async def get_settings() -> SettingsView:
        return _view(settings.get(), secrets, vc_service.model_status)

    @app.put("/settings")
    async def put_settings(patch: SettingsUpdate) -> SettingsView:
        return _view(settings.update(patch), secrets, vc_service.model_status)

    @app.put("/secrets/openrouter", status_code=204)
    @app.put("/secrets/gemini", status_code=204)
    async def put_openrouter_secret(body: SecretUpdate) -> Response:
        try:
            secrets.set_openrouter_key(body.key.strip())
        except KeyringError as exc:
            log.error("keyring write failed: %s", exc)
            raise HTTPException(500, "could not store key in Credential Manager") from exc
        return Response(status_code=204)

    app.include_router(create_memory_router(memory))
    app.include_router(create_projects_router(project_store, RootStore(db), memory))
    app.include_router(create_conversations_router(db))
    app.include_router(create_log_router(db))
    app.include_router(create_ws_router(hub))
    app.add_middleware(
        GuardMiddleware,
        token=config.token,
        port=config.port,
        allowed_origins=config.allowed_origins,
    )
    return app


def _view(s: Settings, secrets: SecretStore, voice_model_status: str = "unloaded") -> SettingsView:
    return SettingsView(
        **s.model_dump(),
        has_openrouter_key=bool(secrets.get_openrouter_key()),
        voice_model_status=voice_model_status,
    )


async def _announce_ready(server: uvicorn.Server, port: int) -> None:
    # Lifespan startup runs before uvicorn binds; wait until it's actually listening (§6.3).
    while not server.started:
        await asyncio.sleep(0.02)
    print(f"READY {port}", flush=True)
