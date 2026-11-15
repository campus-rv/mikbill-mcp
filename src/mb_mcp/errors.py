"""Error type and mapping from Mikbill response envelopes to MCP error codes (SPEC.md, "Error mapping")."""

from __future__ import annotations

from typing import Any

CODES = (
    "upstream_error",
    "auth_failed",
    "timeout",
    "validation",
    "scope_denied",
    "approval_required",
    "conflict",
    "precondition_failed",
    "denied_by_settings",
)

MISSING_PARAM_MARKER = "Не указан обязательный параметр"


class MikbillError(Exception):
    """An error with one of CODES. `status` is the HTTP status when known."""

    def __init__(self, code: str, message: str, *, status: int | None = None,
                 upstream: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        if code not in CODES:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.message = message
        self.status = status
        self.upstream = upstream

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.status is not None:
            out["http_status"] = self.status
        return out


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def is_unauth(env: dict[str, Any]) -> bool:
    """True for the 'not logged in' envelope (HTTP 200, success false, error 1403 / UnAuth)."""
    if env.get("success") is not False:
        return False
    return _as_int(env.get("error")) == 1403 or str(env.get("errortext") or "") == "UnAuth"


def error_from_envelope(env: dict[str, Any], *, status: int | None = 200) -> MikbillError | None:
    """Return a MikbillError for a failed envelope, or None when the call succeeded."""
    if not isinstance(env, dict) or not env:
        return MikbillError("upstream_error", "empty response", status=status)
    if "success" not in env:
        return MikbillError("upstream_error", "response has no success field", status=status, upstream=env)
    if env.get("success") is True:
        return None

    err = _as_int(env.get("error"))
    text = str(env.get("errortext") or "")
    key = str(env.get("errorKey") or "")

    if is_unauth(env):
        return MikbillError("auth_failed", "session is not authenticated", status=status, upstream=env)
    if "denied by settings" in text.lower():
        return MikbillError("denied_by_settings", text, status=status, upstream=env)
    if "DUPLICATE_LOGIN" in text:
        return MikbillError("precondition_failed", f"{text} (the user is probably not in the state this action needs)",
                            status=status, upstream=env)
    if err == 27:
        return MikbillError("precondition_failed", text or "precondition failed (error 27)", status=status, upstream=env)
    if MISSING_PARAM_MARKER in text:
        return MikbillError("validation", f"missing required parameter: {key or text}", status=status, upstream=env)
    if err == 8:
        return MikbillError("conflict", "in use: the object is still referenced (error 8)", status=status, upstream=env)
    if err == 3:
        # Seen on user-group delete: "group is used by subscribers". The text is localized, the code is not.
        return MikbillError("conflict", text or "in use: still referenced by other records (error 3)",
                            status=status, upstream=env)
    if err == 1202:
        return MikbillError("upstream_error", "Mikbill reported an internal error (1202); see its general log",
                            status=status, upstream=env)
    return MikbillError("upstream_error", text or f"upstream error {env.get('error')}", status=status, upstream=env)
