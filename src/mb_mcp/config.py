"""Settings from environment variables (SPEC.md, "Authentication and session" and "Transport")."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("mb_mcp.config")

# `pii` holds no tools: it decides whether identity documents (passport, tax id, birth date) in subscriber records
# are returned or masked.
ALL_SCOPES = ("read", "write", "finance", "account", "admin", "pii")
DEFAULT_SCOPES = ("read", "write")
GATED_SCOPES = ("finance", "account", "admin")
DATA_SCOPES = ("pii",)
DEFAULT_MAX_PAYMENT = 3000.0
DEFAULT_MAX_PAYMENT_TOTAL = 10000.0
DEFAULT_AUTH_MAX_FAILURES = 10
TRANSPORTS = ("stdio", "http")
DEFAULT_TRANSPORT = "stdio"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
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
    password: str = field(repr=False)  # never in repr(), so it cannot end up in a log line or a traceback
    scopes: frozenset[str]
    transport: str = DEFAULT_TRANSPORT  # "stdio" (local) or "http" (Streamable HTTP); MCP_TRANSPORT or --transport
    ca_bundle: str | None = None
    insecure_tls: bool = False
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_bulk: int = 50
    max_payment: float = DEFAULT_MAX_PAYMENT              # MB_MAX_PAYMENT: most money per account in one call
    max_payment_total: float = DEFAULT_MAX_PAYMENT_TOTAL  # MB_MAX_PAYMENT_TOTAL: most money in one bulk call
    audit_file: str | None = None
    auth_max_failures: int = DEFAULT_AUTH_MAX_FAILURES    # MB_AUTH_MAX_FAILURES per client address and minute; 0: off
    http_tokens: tuple[HttpToken, ...] = ()
    allowed_origins: frozenset[str] = frozenset()
    allowed_hosts: frozenset[str] = frozenset()  # extra Host header values (host:port) for proxies and port mappings
    http_host: str = DEFAULT_HTTP_HOST  # MCP_HTTP_HOST; the --host option overrides it
    http_port: int = DEFAULT_HTTP_PORT  # MCP_HTTP_PORT; the --port option overrides it

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, transport: str | None = None) -> Settings:
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

        base_url = _base_url(need("MB_BASE_URL"), allow_http=e.get("MB_INSECURE_HTTP", "") == "1")
        login = need("MB_LOGIN")
        password = _secret(e, "MB_PASSWORD")
        if not password:
            raise ConfigError("MB_PASSWORD (or MB_PASSWORD_FILE) is required")
        max_payment = _positive(e, "MB_MAX_PAYMENT", DEFAULT_MAX_PAYMENT)
        max_payment_total = _positive(e, "MB_MAX_PAYMENT_TOTAL", max(DEFAULT_MAX_PAYMENT_TOTAL, max_payment))
        if max_payment_total < max_payment:
            raise ConfigError("MB_MAX_PAYMENT_TOTAL must not be less than MB_MAX_PAYMENT")

        mode = (transport or e.get("MCP_TRANSPORT", "").strip() or DEFAULT_TRANSPORT).lower()
        if mode not in TRANSPORTS:
            raise ConfigError(f"MCP_TRANSPORT must be one of: {', '.join(TRANSPORTS)}")
        scopes = parse_scopes(e.get("MB_SCOPES", ",".join(DEFAULT_SCOPES)))
        http: dict[str, object] = {}
        if mode == "http":
            tokens = parse_tokens(_secret(e, "MB_HTTP_TOKENS"))
            bearer = _secret(e, "MCP_HTTP_BEARER_TOKEN")
            if bearer:
                tokens = (*tokens, bearer_token(bearer, scopes))
            http = {
                "http_tokens": tokens,
                "allowed_origins": _csv(e.get("MCP_ALLOWED_ORIGINS", "")),
                "allowed_hosts": _csv(e.get("MCP_ALLOWED_HOSTS", "")),
                "http_host": e.get("MCP_HTTP_HOST", "").strip() or DEFAULT_HTTP_HOST,
                "http_port": _port(e.get("MCP_HTTP_PORT", "")),
                "auth_max_failures": _count(e, "MB_AUTH_MAX_FAILURES", DEFAULT_AUTH_MAX_FAILURES),
            }
        return cls(
            base_url=base_url,
            login=login,
            password=password,
            scopes=scopes,
            transport=mode,
            ca_bundle=e.get("MB_CA_BUNDLE") or None,
            insecure_tls=e.get("MB_INSECURE_TLS", "") == "1",
            connect_timeout=_float(e, "MB_TIMEOUT_CONNECT", 5.0),
            read_timeout=_float(e, "MB_TIMEOUT_READ", 30.0),
            max_bulk=int(_float(e, "MB_MAX_BULK", 50)),
            max_payment=max_payment,
            max_payment_total=max_payment_total,
            audit_file=e.get("MB_AUDIT_FILE") or None,
            **http,  # type: ignore[arg-type]
        )


def _secret(e: Mapping[str, str], name: str) -> str:
    """A secret from NAME or, for Docker and Kubernetes secrets, from the file named by NAME_FILE."""
    value = e.get(name, "").strip()
    path = e.get(f"{name}_FILE", "").strip()
    if not path:
        return value
    if value:
        raise ConfigError(f"set {name} or {name}_FILE, not both")
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{name}_FILE {path} cannot be read") from exc


def _base_url(raw: str, *, allow_http: bool) -> str:
    """The Mikbill URL. The login sends a password-equivalent MD5 and every call sends the session cookie, so plain
    http is refused unless MB_INSECURE_HTTP=1 says the link is trusted (a loopback or lab installation)."""
    url = raw.rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("MB_BASE_URL must be an http(s) URL, for example https://admin.example.com")
    if parts.username or parts.password:
        raise ConfigError("MB_BASE_URL must not contain credentials; use MB_LOGIN and MB_PASSWORD")
    if parts.scheme == "http":
        if not allow_http:
            raise ConfigError("MB_BASE_URL is plain http: the login and the session cookie would travel unencrypted. "
                              "Use https, or set MB_INSECURE_HTTP=1 to accept that")
        log.warning("MB_BASE_URL is plain http (MB_INSECURE_HTTP=1): the Mikbill login is not encrypted")
    return url


def _positive(e: Mapping[str, str], name: str, default: float) -> float:
    value = _float(e, name, default)
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be a number greater than 0")
    return value


def _count(e: Mapping[str, str], name: str, default: int) -> int:
    value = _float(e, name, default)
    if not math.isfinite(value) or value < 0 or value != int(value):
        raise ConfigError(f"{name} must be a whole number, 0 or more")
    return int(value)


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
