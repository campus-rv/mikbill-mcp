"""Settings from environment variables (SPEC.md, "Authentication and session" and "Transport")."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping

ALL_SCOPES = ("read", "write", "finance", "account_state", "admin")
DEFAULT_SCOPES = ("read", "write")
GATED_SCOPES = ("finance", "account_state", "admin")


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
    ca_bundle: str | None = None
    insecure_tls: bool = False
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_bulk: int = 50
    audit_file: str | None = None
    http_tokens: tuple[HttpToken, ...] = ()
    allowed_origins: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env

        def need(name: str) -> str:
            v = e.get(name, "").strip()
            if not v:
                raise ConfigError(f"{name} is required")
            return v

        return cls(
            base_url=need("MB_BASE_URL").rstrip("/"),
            login=need("MB_LOGIN"),
            password=need("MB_PASSWORD"),
            scopes=parse_scopes(e.get("MB_SCOPES", ",".join(DEFAULT_SCOPES))),
            ca_bundle=e.get("MB_CA_BUNDLE") or None,
            insecure_tls=e.get("MB_INSECURE_TLS", "") == "1",
            connect_timeout=_float(e, "MB_TIMEOUT_CONNECT", 5.0),
            read_timeout=_float(e, "MB_TIMEOUT_READ", 30.0),
            max_bulk=int(_float(e, "MB_MAX_BULK", 50)),
            audit_file=e.get("MB_AUDIT_FILE") or None,
            http_tokens=parse_tokens(e.get("MB_HTTP_TOKENS", "")),
            allowed_origins=frozenset(o.strip() for o in e.get("MB_ALLOWED_ORIGINS", "").split(",") if o.strip()),
        )


def _float(e: Mapping[str, str], name: str, default: float) -> float:
    raw = e.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


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
