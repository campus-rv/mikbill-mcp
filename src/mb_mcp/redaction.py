"""Redaction of credentials and identity data in tool output, and of everything personal in the audit log
(SPEC.md, "Redaction and logging")."""

from __future__ import annotations

import re
from typing import Any

from .schemas import AUDIT_CLEAR_KEYS

MASK = "***REDACTED***"

# Plaintext credentials Mikbill returns in user records and histories. Always masked in tool output.
OUTPUT_KEYS = frozenset({"password", "router_pass", "router_wep_pass", "pass", "portal_password", "secret"})

# Any other key that names a credential. Mikbill's option and record names are not enumerated (system options,
# integration settings), so the exact list above is not enough: smtp_password, DB_PASS, radius_secret, api_key,
# token and activation_code must be masked too. "passport..." is identity data, handled below, not a password.
_CREDENTIAL_RE = re.compile(
    r"password|passwd|pwd|(^|_)pass($|_)|secret|token|api_?key|private_?key|(^|_)key$|activation_code|code_activation")

# Identity documents and birth data. Masked in tool output unless the caller has the `pii` scope.
IDENTITY_KEYS = frozenset({
    "inn", "date_birth", "date_birth_do", "passportserie", "passportgdevidan", "passportpropiska",
    "passportvoenkomat", "ext_passport_date", "ext_doc_number", "ext_doc_type", "ext_doc_unit_id",
})

# Personal data. Never written to logs.
PERSONAL_KEYS = IDENTITY_KEYS | frozenset({
    "phone", "phones", "mob_tel", "sms_tel", "callback_number", "email", "address", "old_address", "fio", "note",
})

LOG_KEYS = OUTPUT_KEYS | PERSONAL_KEYS

MAX_TEXT_OUTPUT = 4000   # longest string returned in a tool result; the rest is cut and counted


def is_credential_key(key: str) -> bool:
    k = key.lower()
    return k in OUTPUT_KEYS or _CREDENTIAL_RE.search(k) is not None


def redact(value: Any, keys: frozenset[str] = OUTPUT_KEYS) -> Any:
    """Return a copy of `value` with credentials, and the values of `keys` (case-insensitive), replaced by MASK.

    Credentials are recognised by key name (the exact list and the pattern above) and are masked whatever `keys`
    is. Empty values stay empty so a missing password is not shown as redacted.
    """
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and v not in (None, "", 0) and (k.lower() in keys or is_credential_key(k)):
                out[k] = MASK
            else:
                out[k] = redact(v, keys)
        return out
    if isinstance(value, list):
        return [redact(v, keys) for v in value]
    return value


def redact_output(value: Any, *, reveal_identity: bool = False) -> Any:
    """Tool output: credentials are always masked, identity documents unless the caller has the `pii` scope, and
    very long strings are cut (ticket text and notes come from subscribers and can be arbitrarily long)."""
    return clip(redact(value, OUTPUT_KEYS if reveal_identity else OUTPUT_KEYS | IDENTITY_KEYS))


def clip(value: Any, limit: int = MAX_TEXT_OUTPUT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]} [cut: {len(value) - limit} more characters]"
    if isinstance(value, dict):
        return {k: clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [clip(v, limit) for v in value]
    return value


def audit_params(params: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments as they may be written to the audit log.

    An allowlist, not a blocklist: only ids, amounts, switches, enums and dates are kept (AUDIT_CLEAR_KEYS). Every
    other argument is free text that can hold a name, an address, a login, a note or a code, and is masked. A new
    argument is therefore masked until it is added to the list.
    """
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v in (None, "") or (k in AUDIT_CLEAR_KEYS and not is_credential_key(k) and _plain(v)):
            out[k] = v
        else:
            out[k] = MASK
    return out


def _plain(v: Any) -> bool:
    """A value that cannot carry free text: a number, a short token, or a list of those."""
    if isinstance(v, (bool, int, float)):
        return True
    if isinstance(v, str):
        return len(v) <= 32 and re.fullmatch(r"[\w .:+-]*", v, re.ASCII) is not None
    if isinstance(v, list):
        return all(isinstance(x, (bool, int, float)) or (isinstance(x, str) and _plain(x)) for x in v)
    return False
