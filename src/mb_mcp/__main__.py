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
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--list-endpoints", action="store_true", help="print the endpoint table and exit (no network)")
    args = parser.parse_args(argv)

    if args.list_endpoints:
        return _list_endpoints()

    from .config import ConfigError, Settings
    from .server import run_http, run_stdio

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)  # stdout is the MCP channel on stdio
    try:
        settings = Settings.from_env()
        asyncio.run(run_http(settings, args.host, args.port) if args.http else run_stdio(settings))
    except ConfigError as exc:
        print(f"mb-mcp: configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
