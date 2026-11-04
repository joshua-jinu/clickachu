"""Request guard: Host, Origin and Bearer token checks (PLAN §6.6–6.7).

Pure ASGI so it covers both HTTP and WebSocket scopes. Browsers can't set headers on a
WebSocket, so `/ws` skips the Bearer check here and authenticates in-band (api/ws.py).
CORS for the allowlisted renderer origins is answered here too, because the renderer
(localhost:5173 in dev, file:// in prod) is always cross-origin to 127.0.0.1:<port>.
"""

import hmac
import json
import logging
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

DEV_ORIGINS = frozenset({"http://localhost:5173"})
# Electron sends `Origin: file://` for file:// pages; app:// is a future custom protocol.
PROD_ORIGINS = frozenset({"file://"})
PROD_ORIGIN_PREFIXES = ("app://",)

WS_PATH = "/ws"


def normalize_origin(origin: str) -> str:
    # "http://localhost:5173/" -> "http://localhost:5173", but keep "file://" intact.
    return origin[:-1] if origin.endswith("/") and not origin.endswith("://") else origin


def default_origins(extra: Iterable[str] = ()) -> frozenset[str]:
    return DEV_ORIGINS | PROD_ORIGINS | frozenset(normalize_origin(o) for o in extra if o)


def tokens_match(expected: str | None, given: str | None) -> bool:
    """Constant-time compare. `expected is None` means --dev-insecure (auth disabled)."""
    if expected is None:
        return True
    if given is None:
        return False
    return hmac.compare_digest(expected.encode(), given.encode())


class GuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str | None,
        port: int,
        allowed_origins: frozenset[str],
    ) -> None:
        self.app = app
        self.token = token
        self.expected_host = f"127.0.0.1:{port}"
        self.allowed_origins = allowed_origins

    def origin_allowed(self, origin: str) -> bool:
        origin = normalize_origin(origin)
        return origin in self.allowed_origins or origin.startswith(PROD_ORIGIN_PREFIXES)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host")
        origin = headers.get("origin")

        if host != self.expected_host:
            log.warning("rejected request with Host=%r", host)
            await self._reject(scope, receive, send, 403, "bad host")
            return
        # No Origin = non-browser client (Electron main, curl); the token still applies.
        if origin is not None and not self.origin_allowed(origin):
            log.warning("rejected request with Origin=%r", origin)
            await self._reject(scope, receive, send, 403, "bad origin")
            return

        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        if scope["method"] == "OPTIONS" and origin is not None:
            await self._preflight(send, origin)
            return

        auth = headers.get("authorization", "")
        given = auth[7:] if auth.lower().startswith("bearer ") else None
        if not tokens_match(self.token, given):
            await self._reject(scope, receive, send, 401, "unauthorized", origin)
            return

        if origin is None:
            await self.app(scope, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *_cors_headers(origin)]
            await send(message)

        await self.app(scope, receive, send_with_cors)

    async def _preflight(self, send: Send, origin: str) -> None:
        headers = [
            *_cors_headers(origin),
            (b"access-control-allow-methods", b"GET, POST, PUT, PATCH, DELETE, OPTIONS"),
            (b"access-control-allow-headers", b"authorization, content-type"),
            (b"access-control-max-age", b"600"),
        ]
        await send({"type": "http.response.start", "status": 204, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status: int,
        detail: str,
        origin: str | None = None,
    ) -> None:
        if scope["type"] == "websocket":
            # Refuse the handshake; the ASGI server turns this into an HTTP 403.
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": detail}).encode()
        headers = [(b"content-type", b"application/json")]
        if origin is not None:
            headers += _cors_headers(origin)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def _cors_headers(origin: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin.encode("latin-1")),
        (b"vary", b"Origin"),
    ]
