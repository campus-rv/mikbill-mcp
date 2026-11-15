"""MCP server wiring for `mcp` SDK 2.x: handler-based low-level Server, stdio and Streamable HTTP."""

from __future__ import annotations

import logging
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server

from .audit import AuditLog
from .client import MikbillClient
from .config import ConfigError, Settings
from .gating import ApprovalStore
from .http_auth import BearerAuthMiddleware, request_principal, request_scopes
from .runtime import Runtime, to_text

log = logging.getLogger("mb_mcp.server")

SERVER_NAME = "mikbill-mcp"


def build_runtime(settings: Settings, client: Any | None = None) -> Runtime:
    return Runtime(settings, client or MikbillClient(settings), ApprovalStore(), AuditLog(settings.audit_file))


def current_scopes(settings: Settings) -> frozenset[str]:
    """Scopes for the current request: the token's (HTTP) or the server's own (stdio)."""
    scopes = request_scopes.get()
    return settings.scopes if scopes is None else scopes


def build_server(runtime: Runtime) -> Server:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        eps = runtime.tools_for(current_scopes(runtime.settings))
        return types.ListToolsResult(tools=[types.Tool(**runtime.describe(ep)) for ep in eps])

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        envelope = await runtime.execute(params.name, params.arguments or {}, current_scopes(runtime.settings),
                                         request_principal.get())
        return types.CallToolResult(content=[types.TextContent(type="text", text=to_text(envelope))],
                                    isError=not envelope.get("ok"))

    return Server(SERVER_NAME, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def build_http_app(runtime: Runtime, host: str, port: int) -> Any:
    """Streamable HTTP ASGI app at /mcp, wrapped with bearer-token and Origin checks.

    Stateless: every request is independent, so the request's scopes (set by the middleware in a
    context variable) are the ones the handler sees.
    """
    from mcp.server.streamable_http_manager import TransportSecuritySettings

    settings = runtime.settings
    if not settings.http_tokens:
        raise ConfigError("MB_HTTP_TOKENS must define at least one token to serve HTTP")
    server = build_server(runtime)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{host}:{port}", f"127.0.0.1:{port}", f"localhost:{port}"],
        allowed_origins=sorted(settings.allowed_origins),
    )
    app = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, transport_security=security,
                                     host=host)
    return BearerAuthMiddleware(app, settings)


async def run_stdio(settings: Settings) -> None:
    from mcp.server.stdio import stdio_server

    runtime = build_runtime(settings)
    server = build_server(runtime)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await runtime.client.aclose()  # type: ignore[attr-defined]


async def run_http(settings: Settings, host: str, port: int) -> None:
    import uvicorn

    runtime = build_runtime(settings)
    app = build_http_app(runtime, host, port)
    try:
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).serve()
    finally:
        await runtime.client.aclose()  # type: ignore[attr-defined]
