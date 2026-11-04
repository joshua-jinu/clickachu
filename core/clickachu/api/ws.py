"""WebSocket hub (PLAN §7): in-band auth, broadcast, dispatch by message type."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from clickachu.api.protocol import (
    AuthMessage,
    ClientMessage,
    ServerMessage,
    client_message_adapter,
)
from clickachu.api.security import tokens_match

log = logging.getLogger(__name__)

AUTH_FAILED_CODE = 4401

Handler = Callable[[ClientMessage], Awaitable[None]]


class Connection:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._send_lock = asyncio.Lock()

    async def send(self, message: BaseModel) -> None:
        async with self._send_lock:
            await self.ws.send_text(message.model_dump_json())


class Hub:
    def __init__(self, *, token: str | None, auth_timeout_s: float = 2.0) -> None:
        self.token = token
        self.auth_timeout_s = auth_timeout_s
        self._connections: set[Connection] = set()
        self._handlers: dict[str, Handler] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def on(self, message_type: str, handler: Handler) -> None:
        self._handlers[message_type] = handler

    async def broadcast(self, message: ServerMessage) -> None:
        for conn in list(self._connections):
            try:
                await conn.send(message)
            except (WebSocketDisconnect, RuntimeError, OSError) as exc:
                log.info("dropping websocket after send failure: %s", exc)
                self._connections.discard(conn)

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        if not await self._authenticate(ws):
            await ws.close(code=AUTH_FAILED_CODE)
            return

        conn = Connection(ws)
        self._connections.add(conn)
        try:
            while True:
                raw = await ws.receive_text()
                self._dispatch(raw)
        except WebSocketDisconnect:
            pass
        finally:
            self._connections.discard(conn)

    async def _authenticate(self, ws: WebSocket) -> bool:
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=self.auth_timeout_s)
            msg = client_message_adapter.validate_json(raw)
        except TimeoutError:
            log.warning("websocket auth timed out")
            return False
        except (ValidationError, WebSocketDisconnect, KeyError) as exc:
            log.warning("websocket auth failed: %s", exc)
            return False
        return isinstance(msg, AuthMessage) and tokens_match(self.token, msg.token)

    def _dispatch(self, raw: str) -> None:
        try:
            msg = client_message_adapter.validate_json(raw)
        except ValidationError as exc:
            log.warning("ignoring invalid websocket message: %s", exc.errors()[:1])
            return
        handler = self._handlers.get(msg.type)
        if handler is None:
            log.info("no handler for websocket message type %r", msg.type)
            return
        # Run as a task so a long run never blocks the receive loop (cancel must get through).
        task = asyncio.create_task(handler(msg))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.error("websocket handler failed", exc_info=exc)


def create_ws_router(hub: Hub) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await hub.serve(ws)

    return router
