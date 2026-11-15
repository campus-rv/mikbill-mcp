"""Redaction of credentials in tool output and of personal data in logs (SPEC.md, "Redaction and logging")."""

from __future__ import annotations

from typing import Any

MASK = "***REDACTED***"

# Plaintext credentials Mikbill returns in user records and histories. Always masked in tool output.
OUTPUT_KEYS = frozenset({"password", "router_pass", "router_wep_pass", "pass", "portal_password", "secret"})

# Personal data. Masked in logs and audit entries, not in tool output.
PERSONAL_KEYS = frozenset({
    "phone", "phones", "mob_tel", "sms_tel", "callback_number", "email", "inn", "date_birth", "date_birth_do",
    "passportserie", "passportgdevidan", "passportpropiska", "passportvoenkomat", "ext_passport_date",
    "address", "old_address", "fio",
})

LOG_KEYS = OUTPUT_KEYS | PERSONAL_KEYS


def redact(value: Any, keys: frozenset[str] = OUTPUT_KEYS) -> Any:
    """Return a copy of `value` with the values of `keys` (case-insensitive) replaced by MASK.

    Empty values stay empty so a missing password is not shown as redacted.
    """
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in keys and v not in (None, "", 0):
                out[k] = MASK
            else:
                out[k] = redact(v, keys)
        return out
    if isinstance(value, list):
        return [redact(v, keys) for v in value]
    return value
