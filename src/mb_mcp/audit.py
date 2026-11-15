"""Audit log for writes, gated calls, and auth failures (SPEC.md, "Redaction and logging")."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .redaction import LOG_KEYS, redact

log = logging.getLogger("mb_mcp.audit")


class AuditLog:
    """Writes one JSON line per event to the `mb_mcp.audit` logger and, optionally, to a file.

    Parameters are redacted with the personal-data list, so phone numbers, passports, emails and
    passwords never reach the log.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path) if path else None

    def record(self, *, tool: str, scope: str, principal: str, outcome: str,
               params: dict[str, Any] | None = None, reason: str | None = None,
               detail: str | None = None) -> dict[str, Any]:
        event: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool,
            "scope": scope,
            "principal": principal,
            "outcome": outcome,
            "reason": reason,
            "params": redact(params or {}, LOG_KEYS),
        }
        if detail:
            event["detail"] = detail
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        log.info(line)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return event
