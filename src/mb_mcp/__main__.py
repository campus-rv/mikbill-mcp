"""CLI entry point: stdio (default), Streamable HTTP (--http), or an offline endpoint listing."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .endpoints import ALL, EXCLUDED, PENDING


def _list_endpoints() -> int:
    for ep in ALL:
        gate = " [gated]" if ep.gated else ""
        print(f"{ep.scope:14} {ep.tool:30} {ep.uri:40} {ep.status}{gate}")
    print(f"\n{len(ALL)} tools. Excluded: {', '.join(name for name, _ in EXCLUDED)}. "
          f"Pending: {', '.join(name for name, _ in PENDING)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mb-mcp", description="Mikbill admin MCP server")
    parser.add_argument("--transport", choices=("stdio", "http"), default=None,
                        help="stdio (local MCP client, the default) or http (Streamable HTTP); default: MCP_TRANSPORT")
    parser.add_argument("--http", action="store_true", help="same as --transport http")
    parser.add_argument("--host", default=None, help="HTTP bind address (default: MCP_HTTP_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="HTTP port (default: MCP_HTTP_PORT or 8765)")
    parser.add_argument("--list-endpoints", action="store_true", help="print the endpoint table and exit (no network)")
    args = parser.parse_args(argv)

    if args.list_endpoints:
        return _list_endpoints()

    from .config import ConfigError, Settings
    from .server import run_http, run_stdio

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)  # stdout is the MCP channel on stdio
    try:
        settings = Settings.from_env(transport="http" if args.http else args.transport)
        if settings.transport == "http":
            asyncio.run(run_http(settings, args.host or settings.http_host, args.port or settings.http_port))
        else:
            asyncio.run(run_stdio(settings))
    except ConfigError as exc:
        print(f"mb-mcp: configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
