"""Settings from environment variables (SPEC.md, "Authentication and session" and "Transport")."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Mapping

ALL_SCOPES = ("read", "write", "finance", "account_state", "admin")
DEFAULT_SCOPES = ("read", "write")
GATED_SCOPES = ("finance", "account_state", "admin")
TRANSPORTS = ("stdio", "http")
DEFAULT_TRANSPORT = "stdio"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
MIN_BEARER_TOKEN_LENGTH = 16


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class HttpToken:
    id: str
    sha256: str  # hex digest of the bearer token
    scopes: frozenset[str]


@dataclass(frozen=True)
class Settings:
    base_url: str
    login: str
    password: str
    scopes: frozenset[str]
    transport: str = DEFAULT_TRANSPORT  # "stdio" (local) or "http" (Streamable HTTP); MCP_TRANSPORT or --transport
    ca_bundle: str | None = None
    insecure_tls: bool = False
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_bulk: int = 50
    audit_file: str | None = None
    http_tokens: tuple[HttpToken, ...] = ()
    allowed_origins: frozenset[str] = frozenset()
    allowed_hosts: frozenset[str] = frozenset()  # extra Host header values (host:port) for proxies and port mappings
    http_host: str = DEFAULT_HTTP_HOST  # MCP_HTTP_HOST; the --host option overrides it
    http_port: int = DEFAULT_HTTP_PORT  # MCP_HTTP_PORT; the --port option overrides it

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, transport: str | None = None) -> "Settings":
        """Read the settings. `transport` (the command line) overrides MCP_TRANSPORT; the default is stdio.

        The HTTP settings (MCP_HTTP_HOST, MCP_HTTP_PORT, MCP_HTTP_BEARER_TOKEN, MB_HTTP_TOKENS,
        MCP_ALLOWED_ORIGINS, MCP_ALLOWED_HOSTS) are read only when the transport is http. In stdio mode
        they are ignored and not validated, so one env file can serve both modes.
        """
        e = os.environ if env is None else env

        def need(name: str) -> str:
            v = e.get(name, "").strip()
            if not v:
                raise ConfigError(f"{name} is required")
            return v

        mode = (transport or e.get("MCP_TRANSPORT", "").strip() or DEFAULT_TRANSPORT).lower()
        if mode not in TRANSPORTS:
            raise ConfigError(f"MCP_TRANSPORT must be one of: {', '.join(TRANSPORTS)}")
        scopes = parse_scopes(e.get("MB_SCOPES", ",".join(DEFAULT_SCOPES)))
        http: dict[str, object] = {}
        if mode == "http":
            tokens = parse_tokens(e.get("MB_HTTP_TOKENS", ""))
            bearer = e.get("MCP_HTTP_BEARER_TOKEN", "").strip()
            if bearer:
                tokens = (*tokens, bearer_token(bearer, scopes))
            http = {
                "http_tokens": tokens,
                "allowed_origins": _csv(e.get("MCP_ALLOWED_ORIGINS", "")),
                "allowed_hosts": _csv(e.get("MCP_ALLOWED_HOSTS", "")),
                "http_host": e.get("MCP_HTTP_HOST", "").strip() or DEFAULT_HTTP_HOST,
                "http_port": _port(e.get("MCP_HTTP_PORT", "")),
            }
        return cls(
            base_url=need("MB_BASE_URL").rstrip("/"),
            login=need("MB_LOGIN"),
            password=need("MB_PASSWORD"),
            scopes=scopes,
            transport=mode,
            ca_bundle=e.get("MB_CA_BUNDLE") or None,
            insecure_tls=e.get("MB_INSECURE_TLS", "") == "1",
            connect_timeout=_float(e, "MB_TIMEOUT_CONNECT", 5.0),
            read_timeout=_float(e, "MB_TIMEOUT_READ", 30.0),
            max_bulk=int(_float(e, "MB_MAX_BULK", 50)),
            audit_file=e.get("MB_AUDIT_FILE") or None,
            **http,  # type: ignore[arg-type]
        )


def _csv(raw: str) -> frozenset[str]:
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _float(e: Mapping[str, str], name: str, default: float) -> float:
    raw = e.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def _port(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        return DEFAULT_HTTP_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ConfigError("MCP_HTTP_PORT must be a number") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("MCP_HTTP_PORT must be between 1 and 65535")
    return port


def bearer_token(token: str, scopes: frozenset[str]) -> HttpToken:
    """MCP_HTTP_BEARER_TOKEN (HTTP transport only): one plain bearer token that gets every scope the server is configured with.

    Only its SHA-256 is kept in memory, like the tokens from MB_HTTP_TOKENS. The scopes it may use are
    still limited by MB_SCOPES. Use MB_HTTP_TOKENS when different agents need different scopes.
    """
    if len(token) < MIN_BEARER_TOKEN_LENGTH:
        raise ConfigError(f"MCP_HTTP_BEARER_TOKEN must be at least {MIN_BEARER_TOKEN_LENGTH} characters")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return HttpToken(id="bearer", sha256=digest, scopes=scopes)


def parse_scopes(raw: str) -> frozenset[str]:
    scopes = frozenset(s.strip() for s in raw.split(",") if s.strip())
    unknown = scopes - frozenset(ALL_SCOPES)
    if unknown:
        raise ConfigError(f"unknown scopes: {', '.join(sorted(unknown))}")
    return scopes


def parse_tokens(raw: str) -> tuple[HttpToken, ...]:
    """MB_HTTP_TOKENS is a JSON list: [{"id": "agent1", "sha256": "<hex>", "scopes": ["read"]}]."""
    if not raw.strip():
        return ()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError("MB_HTTP_TOKENS is not valid JSON") from exc
    tokens = []
    for item in items:
        try:
            tokens.append(HttpToken(
                id=str(item["id"]),
                sha256=str(item["sha256"]).lower(),
                scopes=parse_scopes(",".join(item.get("scopes", []))),
            ))
        except (KeyError, TypeError) as exc:
            raise ConfigError("each MB_HTTP_TOKENS entry needs id, sha256, scopes") from exc
    return tuple(tokens)
