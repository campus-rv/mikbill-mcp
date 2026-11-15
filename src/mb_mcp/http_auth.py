"""Bearer-token and Origin checks for the Streamable HTTP transport (SPEC.md, "Transport")."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from .config import HttpToken, Settings

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

HEALTH_PATH = "/health"

# Set per request by BearerAuthMiddleware. None means "stdio": use the server-level scopes.
request_scopes: ContextVar[frozenset[str] | None] = ContextVar("mb_request_scopes", default=None)
request_principal: ContextVar[str] = ContextVar("mb_request_principal", default="stdio")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def find_token(tokens: tuple[HttpToken, ...], presented: str) -> HttpToken | None:
    digest = hash_token(presented)
    match: HttpToken | None = None
    for t in tokens:  # compare against every entry so timing does not reveal which one matched
        if hmac.compare_digest(t.sha256, digest):
            match = t
    return match


async def _respond_json(send: Send, status: int, payload: dict[str, Any],
                        headers: list[tuple[bytes, bytes]] | None = None, head: bool = False) -> None:
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store"), *(headers or [])]})
    await send({"type": "http.response.body", "body": b"" if head else body})


async def _respond(send: Send, status: int, message: str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    await _respond_json(send, status, {"error": message}, headers)


class BearerAuthMiddleware:
    """ASGI wrapper: 401 without a valid token, 403 for a disallowed Origin.

    A valid token sets the request's scopes to (token scopes AND server scopes). Tool-level scope
    checks then happen in Runtime.execute(), which returns a scope_denied error.
    """

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan etc.
            await self.app(scope, receive, send)
            return
        if scope.get("path") == HEALTH_PATH and scope.get("method", "GET") in ("GET", "HEAD"):
            # Liveness only: no token, no Origin check, and nothing about the upstream or the configuration
            await _respond_json(send, 200, {"status": "ok"}, head=scope.get("method") == "HEAD")
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}

        origin = headers.get(b"origin")
        if origin is not None and origin.decode("latin-1") not in self.settings.allowed_origins:
            await _respond(send, 403, "origin not allowed")
            return

        auth = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, presented = auth.partition(" ")
        token = find_token(self.settings.http_tokens, presented.strip()) if scheme.lower() == "bearer" and presented else None
        if token is None:
            await _respond(send, 401, "missing or invalid bearer token", [(b"www-authenticate", b"Bearer")])
            return

        scopes_token = request_scopes.set(token.scopes & self.settings.scopes)
        principal_token = request_principal.set(token.id)
        try:
            await self.app(scope, receive, send)
        finally:
            request_scopes.reset(scopes_token)
            request_principal.reset(principal_token)
