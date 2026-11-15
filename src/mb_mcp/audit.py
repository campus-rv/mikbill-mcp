"""Audit log: every tool call that reaches Mikbill, and failed HTTP logins (SPEC.md, "Redaction and logging")."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .config import ConfigError
from .redaction import audit_params

log = logging.getLogger("mb_mcp.audit")


class AuditError(Exception):
    """The audit file could not be written for a record that must be on file before the call goes out."""


class AuditLog:
    """Writes one JSON line per event to the `mb_mcp.audit` logger and, optionally, to a file.

    - The file is opened once at start-up, so a wrong path stops the server instead of failing on the first write.
    - A call that changes data is recorded twice with the same `id`: `attempt` before the request is sent
      (`required=True`: if that cannot be written, the call is not sent) and the outcome after it. An outcome
      that cannot be written is reported on the logger and never changes the result of a call that already ran.
    - Arguments go through an allowlist (redaction.audit_params): ids, amounts and switches are kept, every
      free-text value is masked.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            try:
                with self._path.open("a", encoding="utf-8"):
                    pass
            except OSError as exc:
                raise ConfigError(f"MB_AUDIT_FILE {path} cannot be opened for writing: "
                                  f"{exc.strerror or type(exc).__name__}") from exc

    def record(self, *, tool: str, scope: str, principal: str, outcome: str,
               params: dict[str, Any] | None = None, reason: str | None = None,
               detail: str | None = None, call_id: str | None = None, rows: int | None = None,
               required: bool = False) -> dict[str, Any]:
        event: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool,
            "scope": scope,
            "principal": principal,
            "outcome": outcome,
            "reason": reason,
            "params": audit_params(params or {}),
        }
        if call_id:
            event["id"] = call_id
        if rows is not None:
            event["rows"] = rows
        if detail:
            event["detail"] = detail
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
        log.info(line)
        if self._path is not None:
            try:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError as exc:
                log.error("audit file %s could not be written (%s); the event above is only in this log",
                          self._path, exc.strerror or type(exc).__name__)
                if required:
                    raise AuditError(str(exc)) from exc
        return event
