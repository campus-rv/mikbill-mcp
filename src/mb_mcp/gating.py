"""Dry-run approval tokens for gated tools (SPEC.md, "Gated-tool mechanics")."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

TTL_SECONDS = 300


def params_hash(tool: str, params: dict[str, Any]) -> str:
    canonical = json.dumps({"tool": tool, "params": params}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class _Entry:
    digest: str
    expires: float


class ApprovalStore:
    """One-time tokens bound to the exact tool and params.

    A token is issued by a dry-run and consumed by the executing call. It cannot be reused,
    cannot be used for different params, and expires after TTL_SECONDS.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic, ttl: float = TTL_SECONDS) -> None:
        self._now = now
        self._ttl = ttl
        self._tokens: dict[str, _Entry] = {}

    def issue(self, tool: str, params: dict[str, Any]) -> str:
        self._purge()
        token = secrets.token_urlsafe(24)
        self._tokens[token] = _Entry(params_hash(tool, params), self._now() + self._ttl)
        return token

    def consume(self, token: str | None, tool: str, params: dict[str, Any]) -> bool:
        """Return True and invalidate the token when it matches; False otherwise. A wrong
        digest also invalidates the token, so a leaked token cannot be probed."""
        if not token:
            return False
        self._purge()
        entry = self._tokens.pop(token, None)
        if entry is None:
            return False
        return secrets.compare_digest(entry.digest, params_hash(tool, params))

    @property
    def ttl(self) -> float:
        return self._ttl

    def _purge(self) -> None:
        now = self._now()
        for t in [t for t, e in self._tokens.items() if e.expires <= now]:
            del self._tokens[t]
