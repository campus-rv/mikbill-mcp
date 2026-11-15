"""Bearer-token and Origin checks for the Streamable HTTP transport (SPEC.md, "Transport")."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from .audit import AuditLog
from .config import HttpToken, Settings

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

HEALTH_PATH = "/health"
FAILURE_WINDOW = 60.0      # seconds
MAX_TRACKED_CLIENTS = 10000

# Set per request by BearerAuthMiddleware. None means the middleware did not run: the server-level scopes on stdio,
# no scopes at all over HTTP (server.resolve_scopes).
request_scopes: ContextVar[frozenset[str] | None] = ContextVar("mb_request_scopes", default=None)
request_principal: ContextVar[str] = ContextVar("mb_request_principal", default="stdio")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def find_token(tokens: tuple[HttpToken, ...], presented: str) -> HttpToken | None:
    digest = hash_token(presented)
    match: HttpToken | None = None
    for t in tokens:  # compare against every entry so timing does not reveal which one matched
        if hmac.compare_digest(t.sha256.encode("utf-8"), digest.encode("utf-8")):
            match = t
    return match


class FailureLimiter:
    """Counts failed logins per client address in a sliding window.

    Once an address has `limit` failures inside the window, its requests are refused with 429 before the token is
    looked at, until old failures leave the window. That makes guessing a weak token slow. It keys on the address
    the server sees: behind a reverse proxy that is the proxy, so throttle there as well, or set
    MB_AUTH_MAX_FAILURES=0 to turn this off.
    """

    def __init__(self, limit: int, window: float = FAILURE_WINDOW, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, client: str) -> deque[float] | None:
        q = self._failures.get(client)
        if q is None:
            return None
        cutoff = self._clock() - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del self._failures[client]
            return None
        return q

    def count(self, client: str) -> int:
        """Failures of this client inside the window."""
        q = self._recent(client)
        return len(q) if q is not None else 0

    def retry_after(self, client: str) -> int:
        """Seconds until the client may try again, 0 when it is not blocked."""
        if self.limit <= 0:
            return 0
        q = self._recent(client)
        if q is None or len(q) < self.limit:
            return 0
        return max(1, int(q[0] + self.window - self._clock()) + 1)

    def fail(self, client: str) -> bool:
        """Record a failure. True when this one reached the limit."""
        if self.limit <= 0:
            return False
        q = self._recent(client)  # drops failures that have left the window
        if q is None:
            if len(self._failures) >= MAX_TRACKED_CLIENTS:
                self._failures.pop(next(iter(self._failures)))  # bounded memory under a flood of addresses
            q = self._failures[client] = deque()
        q.append(self._clock())
        return len(q) == self.limit


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
    """ASGI wrapper: 401 without a valid token, 403 for a disallowed Origin, 429 after repeated failures.

    A valid token sets the request's scopes to (token scopes AND server scopes). Tool-level scope
    checks then happen in Runtime.execute(), which returns a scope_denied error. Failed logins are written to
    the audit log (the first of a burst and the one that triggers the block, not every attempt).
    """

    def __init__(self, app: Callable[..., Awaitable[None]], settings: Settings,
                 audit: AuditLog | None = None, limiter: FailureLimiter | None = None) -> None:
        self.app = app
        self.settings = settings
        self.audit = audit
        self.limiter = limiter or FailureLimiter(settings.auth_max_failures)

    def _audit(self, client: str, outcome: str, detail: str) -> None:
        if self.audit is not None:
            self.audit.record(tool="-", scope="-", principal=client, outcome=outcome, detail=detail)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan etc.
            await self.app(scope, receive, send)
            return
        if scope.get("path") == HEALTH_PATH and scope.get("method", "GET") in ("GET", "HEAD"):
            # Liveness only: no token, no Origin check, and nothing about the upstream or the configuration
            await _respond_json(send, 200, {"status": "ok"}, head=scope.get("method") == "HEAD")
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        client = str((scope.get("client") or ("unknown",))[0])

        origin = headers.get(b"origin")
        if origin is not None and origin.decode("latin-1") not in self.settings.allowed_origins:
            await _respond(send, 403, "origin not allowed")
            return

        wait = self.limiter.retry_after(client)
        if wait:
            await _respond(send, 429, "too many failed logins, try again later", [(b"retry-after", str(wait).encode())])
            return

        auth = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, presented = auth.partition(" ")
        token = find_token(self.settings.http_tokens, presented.strip()) if scheme.lower() == "bearer" and presented else None
        if token is None:
            first = self.limiter.count(client) == 0
            blocked = self.limiter.fail(client)
            if blocked:
                self._audit(client, "auth_blocked", f"{self.limiter.limit} failed logins in {int(self.limiter.window)} s; "
                                                    "further requests from this address get 429")
            elif first or self.limiter.limit <= 0:
                self._audit(client, "auth_failed", "missing or invalid bearer token")
            await _respond(send, 401, "missing or invalid bearer token", [(b"www-authenticate", b"Bearer")])
            return

        scopes_token = request_scopes.set(token.scopes & self.settings.scopes)
        principal_token = request_principal.set(token.id)
        try:
            await self.app(scope, receive, send)
        finally:
            request_scopes.reset(scopes_token)
            request_principal.reset(principal_token)
