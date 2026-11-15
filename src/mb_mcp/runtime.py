"""Transport-independent tool runtime: validation, scopes, dry-run, audit, execution, shaping.

The MCP server (server.py) only translates between MCP messages and Runtime.execute().
"""

from __future__ import annotations

import datetime
import json
import math
import re
import time
import uuid
from decimal import Decimal
from typing import Any, Protocol

from .audit import AuditError, AuditLog
from .config import Settings
from .endpoints import ALL, Endpoint
from .errors import MikbillError
from .redaction import redact, redact_output
from .schemas import (
    COUNT_FIELDS,
    DATE_FIELDS,
    DETAIL,
    FIELD_HELP,
    FLAG_FIELDS,
    ID_FIELDS,
    MAX_ROWS,
    MAX_TEXT_INPUT,
    MONEY_FIELDS,
    PAYMENT_FIELDS,
    PAYMENT_TYPE_KINDS,
    SCALAR_TYPES,
    SCHEMAS,
    SEARCH_INTERNET,
    SEARCH_MATCH,
    SEARCH_STATES,
    TARGET_ID_FIELDS,
    ToolSchema,
)

USER_URI = "/json/users/getuserdatambp"
USER_STATE_KEYS = ("useruid", "user", "state", "status", "deletedtable")

ENABLE_USERS_URI = "/json/users/enableusersmulti"
VKL_URI = "/json/users/vkluserflex"

TICKET_LIST_URI = "/extjs/ticketview/getticketslist2x"
STUFF_URI = "/json/index/getstuffproper"
TICKET_STATE_KEYS = ("statustypeid", "categoryid", "prioritytypeid", "rating", "creationdate")
TICKET_TARGET_TOOLS = {"mb_set_ticket_status", "mb_delete_tickets"}
STATUS_CODES = {"open": 1, "closed": 2, "in_work": 3, "performed": 4}
ZERO_DATE = "0000-00-00 00:00:00"

# Delete tools whose targets are users: the dry-run and the result report each user's state.
# The admin UI's operation types (bughtypeid), one list per operation form. Mikbill accepts any bughtypeid (9999 was
# recorded as a payment with an empty type), so payments are checked against these lists before they are sent.
PAY_TYPE_URIS = {
    "fee": "/json/users/getpaytypeabonplatalistflex",
    "promotion": "/json/users/getpaytypeakciyalistflex",
    "correction": "/json/users/getpaytypepereschetlistflex",
    "connection": "/json/users/getpaytypepodkllistflex",
}
PAYMENT_TYPE_TOOLS = {"mb_create_payment", "mb_bulk_payment"}
PAY_TYPES_TTL = 300.0

USER_TARGET_TOOLS = {
    "mb_delete_user", "mb_bulk_delete_users", "mb_delete_disconnected_user", "mb_delete_user_full",
    "mb_disconnect_user", "mb_freeze_user", "mb_bulk_disable_users", "mb_bulk_enable_users",
    "mb_bulk_unfreeze_users", "mb_bulk_enable_internet",
    "mb_bulk_payment", "mb_bulk_issue_credit", "mb_bulk_remove_credit",
    "mb_receive_payment", "mb_set_credit",  # a single target: the preview shows who it is
}
READ_BACK_TOOLS = {"mb_delete_user", "mb_bulk_delete_users", "mb_delete_disconnected_user", "mb_disconnect_user"}

# Dependency checks: tool -> (uri, parameters to send). They run in the preview (the result is shown) and again
# before the delete itself, which is refused with `conflict` when the check fails, unless `force` is true.
CHECKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "mb_delete_settlement": ("/json/options/checkdeletesettlement", ("settlementid",)),
    # Mikbill's delete endpoints for these do NOT enforce the check (verified on the demo: a lane, neighborhood and
    # settlement were deleted while a house still pointed at them), so the server has to.
    "mb_delete_lane": ("/extjs/options/checkdellane", ("laneid",)),
    "mb_delete_neighborhood": ("/json/options/checkdeleteneighborhood", ("neighborhoodid",)),
    "mb_delete_packet": ("/json/tarif/delchecktarifflex", ("gid",)),
}

_UNCHECKED_BY_MIKBILL = ("Mikbill itself does not stop this delete when other records depend on it, so the server runs "
                         "the dependency check first and refuses with conflict unless force is true.")

# Shown in the tool description and in previews. Written for the model and the user, not for maintainers.
WARNINGS: dict[str, str] = {
    "mb_delete_settlement": _UNCHECKED_BY_MIKBILL,
    "mb_delete_neighborhood": _UNCHECKED_BY_MIKBILL,
    "mb_delete_lane": _UNCHECKED_BY_MIKBILL,
    "mb_delete_house": "Mikbill refuses (conflict) while subscribers, soft-deleted ones included, are registered in "
                       "the house.",
    "mb_delete_user_group": "The number of subscribers in the group is not checked first. Deleting a group that "
                            "subscribers use may orphan them.",
    "mb_delete_user": "Soft delete: the account stays in the database with state 4 and deletedtable 1.",
    "mb_bulk_delete_users": "Soft delete: accounts stay in the database with state 4 and deletedtable 1.",
    "mb_delete_disconnected_user": "Soft delete of a disconnected user: state 4, deletedtable 1.",
    "mb_delete_user_full": "Mikbill may refuse this with 'Denied by settings'.",
    "mb_delete_real_ip": "Deletes a single address per call.",
    "mb_set_personal_service": "The service must have a configured portal. Services on the Wink portal (18) fail "
                               "unless that integration is set up.",
    "mb_use_voucher": "A valid voucher code adds its nominal to the balance.",
    "mb_change_tariff": "The change is scheduled, not applied: the user's gid reads 0 until it takes effect, and "
                        "personal-service calls fail with precondition_failed in the meantime.",
    "mb_restore_user": "Re-enables a soft-deleted account. With gid it also assigns that tariff, which can charge the "
                       "tariff's first fee. The user must currently be deleted (state 4).",
    "mb_delete_user_device": "Removes the device from the subscriber. Use mb_get_user_devices for the devid.",
    "mb_delete_dev_type": "A device type that a device still uses is refused.",
    "mb_delete_tickets": "Deletes the listed tickets. This cannot be undone.",
    "mb_delete_ticket_note": "Deletes one ticket note. This cannot be undone.",
}


class ClientLike(Protocol):
    async def call(self, uri: str, params: dict[str, Any] | None = None, *, allow_empty: bool = False,
                   no_success_key: bool = False) -> dict[str, Any]:
        ...


_UNSET = object()


def _is_bulk(schema: ToolSchema) -> bool:
    """Bulk tools act on a list of subscribers (uids) or tickets (ticket_ids). Gated bulk tools preview by default."""
    return schema.uids or bool(schema.ticket_ids)


def _as_bool(value: Any, name: str = "dry_run") -> bool:
    if not isinstance(value, bool):
        raise MikbillError("validation", f"{name} must be true or false")
    return value


def _ok(data: Any, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "status": 200, "data": data, **extra}


def _fail(err: MikbillError) -> dict[str, Any]:
    return {"ok": False, "error": err.to_dict()}


class Runtime:
    def __init__(self, settings: Settings, client: ClientLike, audit: AuditLog | None = None) -> None:
        self.settings = settings
        self.client = client
        self.audit = audit or AuditLog(settings.audit_file)
        self._by_name = {ep.tool: ep for ep in ALL}
        self._stuff_id: int | None = None  # the session's own staff id, fetched once for ticket status changes
        self._pay_types: tuple[float, list[dict[str, Any]]] | None = None  # (fetched at, rows) of the type lists

    # ---- listing -------------------------------------------------------------------------

    def tools_for(self, scopes: frozenset[str]) -> list[Endpoint]:
        allowed = scopes & self.settings.scopes
        return [ep for ep in ALL if ep.scope in allowed]

    def describe(self, ep: Endpoint) -> dict[str, Any]:
        """MCP tool definition: name, description, inputSchema. Every argument has a type and a description."""
        schema = SCHEMAS[ep.tool]
        props: dict[str, Any] = {f: self._prop(schema, f) for f in schema.fields}
        for key, options in schema.enums.items():
            props[key] = {"type": "string" if isinstance(options[0], str) else "integer", "enum": list(options)}
        for key in props:
            text = schema.help.get(key) or FIELD_HELP.get(key)
            if text:
                props[key]["description"] = text
        if schema.user_search:
            props["states"] = {"type": "array", "items": {"type": "string", "enum": list(SEARCH_STATES)},
                               "minItems": 1, "uniqueItems": True,
                               "description": "Only subscribers in these states. Omit for all states, deleted included."}
            props["user_labels"] = {"type": "array", "items": {"type": "integer"}, "minItems": 1, "uniqueItems": True,
                                    "description": "Only subscribers with these label ids."}
            props["internet"] = {"type": "string", "enum": list(SEARCH_INTERNET),
                                 "description": "Only subscribers whose internet is on or off. Omit for both."}
            props["match"] = {"type": "string", "enum": list(SEARCH_MATCH), "default": "all",
                              "description": "all: every filter must match (default). any: at least one filter "
                                             "must match; not allowed together with states or internet."}
        if schema.row_limit:
            props["limit"] = {"type": "integer", "minimum": 1, "maximum": MAX_ROWS, "default": schema.row_limit,
                              "description": "Most rows to return. The response says if more matched "
                                             "(truncated, next_offset)."}
            props["offset"] = {"type": "integer", "minimum": 0, "default": 0,
                               "description": "Rows to skip, to read the next page: pass next_offset from a "
                                              "truncated response."}
        if schema.summary:
            props["detail"] = {"type": "string", "enum": list(DETAIL), "default": "summary",
                               "description": "summary (default) returns these fields per row: "
                                              + ", ".join(schema.summary) + ". full returns the whole record, with "
                                              "contacts and notes; ask for it only when you need them."}
        required = list(schema.required)
        if schema.ticket_ids:
            props["ticket_ids"] = {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1,
                                   "maxItems": self.settings.max_bulk, "uniqueItems": True,
                                   "description": "Ticket ids."}
            required.append("ticket_ids")
        if schema.uids:
            props["uids"] = {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1,
                             "maxItems": self.settings.max_bulk, "uniqueItems": True,
                             "description": "Subscriber uids. Sent to Mikbill as set_users_list."}
            required.append("uids")
        if ep.tool in CHECKS:
            props["force"] = {"type": "boolean", "default": False,
                              "description": "false (default): the delete is refused when the dependency check finds "
                                             "records that still use this object. true: delete anyway and leave "
                                             "them pointing at nothing. Only on the user's explicit request."}
        if ep.gated:
            bulk = _is_bulk(schema)
            props["dry_run"] = {"type": "boolean", "default": bulk,
                                "description": "true returns a preview (targets and their current state, the exact "
                                               "request, warnings) and sends nothing. Default: "
                                               + ("true for this bulk tool, so a first call previews; pass false to "
                                                  "execute." if bulk else "false (the call executes).")}
            props["reason"] = {"type": "string", "minLength": 3, "description": "Why this action is needed. Logged."}
            required.append("reason")
        elif schema.field_scopes:
            props["reason"] = {"type": "string", "minLength": 3,
                               "description": "Why this change is needed. Required when one of "
                                              + ", ".join(sorted(schema.field_scopes)) + " is sent. Logged."}
        return {
            "name": ep.tool,
            "description": self._description(ep),
            "inputSchema": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
        }

    def _prop(self, schema: ToolSchema, name: str) -> dict[str, Any]:
        """JSON Schema of one argument, from its field type (schemas.py)."""
        if name in PAYMENT_FIELDS:
            return {"type": "number", "exclusiveMinimum": 0, "maximum": self.settings.max_payment}
        if name in MONEY_FIELDS:
            return {"type": "number", "minimum": 0}
        if name in FLAG_FIELDS:
            return {"type": "integer", "enum": [0, 1]}
        if name in DATE_FIELDS:
            return {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
        if name in ID_FIELDS or name in COUNT_FIELDS or name in schema.integers:
            return {"type": "integer", "minimum": _minimum(schema, name)}
        return {"type": SCALAR_TYPES}

    def _description(self, ep: Endpoint) -> str:
        parts = [ep.desc or ep.tool.removeprefix("mb_").replace("_", " ").capitalize() + "."]
        if ep.tool in WARNINGS:
            parts.append(WARNINGS[ep.tool])
        if ep.gated:
            parts.append(f"Requires the {ep.scope} scope and a reason. The client should ask the user before calling.")
            if _is_bulk(SCHEMAS[ep.tool]):
                parts.append("Bulk tool: previews by default, pass dry_run false to execute.")
        if ep.status in ("blocked", "denied"):
            parts.append(f"Status on the reference installation: {ep.status}.")
        return " ".join(parts)

    # ---- execution -----------------------------------------------------------------------

    async def execute(self, name: str, arguments: dict[str, Any] | None, scopes: frozenset[str],
                      principal: str = "stdio") -> dict[str, Any]:
        arguments = dict(arguments or {})
        ep = self._by_name.get(name)
        if ep is None:
            return _fail(MikbillError("validation", f"unknown tool: {name}"))
        granted = scopes & self.settings.scopes
        if ep.scope not in granted:
            self.audit.record(tool=name, scope=ep.scope, principal=principal, outcome="scope_denied")
            return _fail(MikbillError("scope_denied", f"scope '{ep.scope}' is not granted"))

        gated = ep.gated
        schema = SCHEMAS[name]
        # Approval is the client's job (the user confirms the call in the harness). The server keeps scopes, a
        # reason for the audit log, limits on ids and amounts, and a preview that is on by default for bulk tools.
        dry_run_arg = arguments.pop("dry_run", _UNSET) if gated else _UNSET
        reason = arguments.pop("reason", None) if gated or schema.field_scopes else None
        force_arg = arguments.pop("force", False) if name in CHECKS else False
        try:
            dry_run = _is_bulk(schema) if dry_run_arg is _UNSET else _as_bool(dry_run_arg)
            force = _as_bool(force_arg, "force")
            args = self._validate(schema, arguments)
        except MikbillError as err:
            return _fail(err)

        # Some arguments carry a change that belongs to another scope (a tariff change through a plain edit).
        elevated = {schema.field_scopes[k] for k in args if k in schema.field_scopes}
        scope_label = "+".join([ep.scope, *sorted(elevated - {ep.scope})])
        if not elevated <= granted:
            need = sorted(elevated - granted)
            fields = sorted(k for k in args if schema.field_scopes.get(k) in need)
            self.audit.record(tool=name, scope=scope_label, principal=principal, outcome="scope_denied", params=args)
            return _fail(MikbillError("scope_denied", f"{', '.join(fields)} on {name} needs the "
                                                      f"{' and '.join(need)} scope, which is not granted"))
        if (gated or elevated) and not (isinstance(reason, str) and len(reason.strip()) >= 3):
            return _fail(MikbillError("validation", "reason is required (at least 3 characters)"))

        if name in PAYMENT_TYPE_TOOLS:
            try:
                await self._check_payment_type(args["bughtypeid"])
            except MikbillError as err:
                return _fail(err)

        if gated and dry_run:
            try:
                return _ok(await self._dry_run(ep, schema, args), dry_run=True)
            except MikbillError as err:
                if err.code != "validation":
                    self.audit.record(tool=name, scope=scope_label, principal=principal, outcome=err.code,
                                      params=args, reason=reason, detail=f"preview: {err.message}")
                return _fail(err)

        # A call that changes data is on record before it is sent. If that record cannot be written, nothing is
        # sent. The outcome record afterwards can never turn a call that ran into a failure.
        call_id = uuid.uuid4().hex[:12]
        record: dict[str, Any] = {"tool": name, "scope": scope_label, "principal": principal, "params": args,
                                  "reason": reason, "call_id": call_id}
        if not ep.is_read:
            try:
                self.audit.record(outcome="attempt", required=True, **record)
            except AuditError:
                return _fail(MikbillError("audit_unavailable", "the audit log cannot be written, so the call was not "
                                                               "sent to Mikbill"))
        try:
            result = await self._run(ep, schema, args, force=force, reveal_identity="pii" in granted)
        except MikbillError as err:
            if not (ep.is_read and err.code == "validation"):
                self.audit.record(outcome=err.code, detail=err.message, **record)
            return _fail(err)
        except Exception as exc:
            self.audit.record(outcome="error", detail=type(exc).__name__, **record)
            raise
        data = result.get("data")
        self.audit.record(outcome="ok", rows=len(data) if isinstance(data, list) else None, **record)
        return result

    # ---- validation ----------------------------------------------------------------------

    def _validate(self, schema: ToolSchema, arguments: dict[str, Any]) -> dict[str, Any]:
        id_lists = ({"uids"} if schema.uids else set()) | ({"ticket_ids"} if schema.ticket_ids else set())
        search_args = {"states", "user_labels", "internet", "match"} if schema.user_search else set()
        allowed = (set(schema.fields) | id_lists | search_args | ({"limit", "offset"} if schema.row_limit else set())
                   | ({"detail"} if schema.summary else set()))
        unknown = set(arguments) - allowed
        if unknown:
            raise MikbillError("validation", f"unknown parameters: {', '.join(sorted(unknown))}")
        missing = [f for f in schema.required if arguments.get(f) in (None, "")]
        missing += [name for name in sorted(id_lists) if not arguments.get(name)]
        if missing:
            raise MikbillError("validation", f"missing required parameters: {', '.join(missing)}")
        out: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in id_lists:
                out[key] = self._validate_ids(key, value)
            elif key in ("states", "user_labels") and schema.user_search:
                out[key] = value  # checked in _validate_search
            elif key == "detail" and schema.summary:
                if value not in DETAIL:
                    raise MikbillError("validation", f"detail must be one of: {', '.join(DETAIL)}")
                out[key] = value
            else:
                out[key] = self._check_value(schema, key, value)
        for key, options in schema.enums.items():
            if key in out and (isinstance(out[key], bool) or out[key] not in options):
                raise MikbillError("validation", f"{key} must be one of: {', '.join(str(o) for o in options)}")
        if schema.uids:
            for key in PAYMENT_FIELDS & set(out):  # the same amount goes to every listed account
                total = Decimal(str(out[key])) * len(out["uids"])
                if total > Decimal(str(self.settings.max_payment_total)):
                    raise MikbillError("validation", f"{key} x {len(out['uids'])} accounts = {total}, above the limit "
                                                     f"of {self.settings.max_payment_total:g} per bulk call "
                                                     "(MB_MAX_PAYMENT_TOTAL)")
        if schema.user_search:
            self._validate_search(schema, out)
        return out

    def _check_value(self, schema: ToolSchema, key: str, value: Any) -> Any:
        """One argument, checked by its field type. Mikbill parses leniently, so nothing that names a record or an
        amount is passed on unchecked."""
        if key == "limit":
            return _whole_number(key, value, minimum=1, maximum=MAX_ROWS)
        if key == "offset":
            return _whole_number(key, value, minimum=0)
        if key in PAYMENT_FIELDS:
            return _amount(key, value, positive=True, maximum=self.settings.max_payment)
        if key in MONEY_FIELDS:
            return _amount(key, value)
        if key in FLAG_FIELDS:
            return _flag(key, value)
        if key in DATE_FIELDS:
            return _date(key, value)
        if key in ID_FIELDS or key in COUNT_FIELDS or key in schema.integers:
            return _whole_number(key, value, minimum=_minimum(schema, key))
        return _scalar(key, value)

    @staticmethod
    def _validate_search(schema: ToolSchema, out: dict[str, Any]) -> None:
        for key in [k for k, v in out.items() if isinstance(v, str) and not v.strip()]:
            del out[key]  # an empty text field is "not set", as in the UI
        if "states" in out:
            states = out["states"]
            if (not isinstance(states, list) or not states or len(set(map(str, states))) != len(states)
                    or any(st not in SEARCH_STATES for st in states)):
                raise MikbillError("validation", f"states must be a non-empty array of: {', '.join(SEARCH_STATES)}")
        if "user_labels" in out:
            labels = out["user_labels"]
            if (not isinstance(labels, list) or not labels or len(set(map(str, labels))) != len(labels)
                    or not all(isinstance(x, int) and not isinstance(x, bool) for x in labels)):
                raise MikbillError("validation", "user_labels must be a non-empty array of integer label ids")
        for key, options in (("internet", SEARCH_INTERNET), ("match", SEARCH_MATCH)):
            if key in out and out[key] not in options:
                raise MikbillError("validation", f"{key} must be one of: {', '.join(options)}")
        filters = [k for k in out if k not in ("match", "limit", "offset", "detail")]
        if not filters:
            raise MikbillError("validation", "at least one search filter is required (an empty search returns every "
                                             "subscriber)")
        if out.get("match") == "any" and ("states" in out or "internet" in out):
            # verified: with op=0 Mikbill ORs the status and internet selectors with the other filters (gid 38 plus
            # state normal returned the union, 61 rows), which is never what "states" is meant to do
            raise MikbillError("validation", "match any cannot be combined with states or internet: Mikbill would "
                                             "add them to the result instead of narrowing it. Use match all.")
        if "uid" in out and len(filters) > 1:
            raise MikbillError("validation", "uid cannot be combined with other filters: Mikbill ignores them when uid "
                                             "is given")

    def _validate_ids(self, name: str, value: Any) -> list[int]:
        if not isinstance(value, list) or not value or not all(isinstance(u, int) and not isinstance(u, bool) for u in value):
            raise MikbillError("validation", f"{name} must be a non-empty array of integers")
        if any(u < 1 for u in value):
            raise MikbillError("validation", f"{name} must be ids of 1 or more")
        if len(set(value)) != len(value):
            raise MikbillError("validation", f"{name} must not contain duplicates")
        if len(value) > self.settings.max_bulk:
            raise MikbillError("validation", f"too many {name} (limit {self.settings.max_bulk}, MB_MAX_BULK)")
        return list(value)

    # ---- request building ----------------------------------------------------------------

    @staticmethod
    def build_params(schema: ToolSchema, args: dict[str, Any]) -> dict[str, str]:
        """Form parameters sent to Mikbill: defaults, renames, fixed values, uids, string encoding."""
        merged: dict[str, Any] = {**schema.defaults, **args}
        if schema.user_search:
            return Runtime._search_params(merged)
        uids = merged.pop("uids", None)
        ticket_ids = merged.pop("ticket_ids", None)
        out: dict[str, Any] = {schema.rename.get(k, k): v for k, v in merged.items()}
        out.update(schema.fixed)
        if uids is not None:
            out["set_users_list"] = ",".join(str(u) for u in uids)
        if ticket_ids is not None:
            if schema.ticket_ids == "set_list":
                out["set_list"] = ",".join(str(t) for t in ticket_ids)
            else:  # "enumerate": the UI sends ticketid0, ticketid1, ...
                for i, t in enumerate(ticket_ids):
                    out[f"ticketid{i}"] = t
        return {k: _form_value(v) for k, v in out.items()}

    @staticmethod
    def _search_params(args: dict[str, Any]) -> dict[str, str]:
        """The searchflex form the admin UI's search tab sends. Empty filters are left out, as the UI does."""
        out: dict[str, Any] = {k: v for k, v in args.items()
                               if k not in ("states", "user_labels", "internet", "match", "limit", "offset", "detail")}
        states = args.get("states") or []
        out["search_all_states"] = 0 if states else 1
        for flag in SEARCH_STATES.values():
            out[flag] = 0
        for state in states:
            out[SEARCH_STATES[state]] = 1
        out["search_internet"] = SEARCH_INTERNET.get(str(args.get("internet")), 0)
        out["op"] = SEARCH_MATCH[args.get("match", "all")]
        out["search_display_all"] = 1
        if args.get("user_labels"):
            out["user_labels"] = json.dumps(args["user_labels"])
        return {k: _form_value(v) for k, v in out.items()}

    async def _params(self, ep: Endpoint, schema: ToolSchema, args: dict[str, Any]) -> dict[str, str]:
        if ep.tool == "mb_set_ticket_status":
            return await self._status_params(args)
        return self.build_params(schema, args)

    async def _status_params(self, args: dict[str, Any]) -> dict[str, str]:
        """The putticketin request the admin UI sends for each status change (verified on the demo)."""
        status = args["status"]
        if "rating" in args and status != "closed":
            raise MikbillError("validation", "rating is only used with status 'closed'")
        sid = await self._stuffid()
        code = STATUS_CODES[status]
        p: dict[str, Any] = {"set_list": ",".join(str(t) for t in args["ticket_ids"]), "statustypeid": code,
                             "rating": args.get("rating", 0)}
        if code == 1:  # open (take back)
            p.update(closingdate=ZERO_DATE, performed_by_stuffid=sid, inworkdate=ZERO_DATE, closed_by_stuffid=0,
                     performed_without_on_site_visit=0, inwork_by_stuffid=0, can_not_be_performed=0,
                     performingdate=ZERO_DATE)
        elif code == 3:  # in work
            p.update(closingdate=ZERO_DATE, performed_by_stuffid=0, closed_by_stuffid=0,
                     performed_without_on_site_visit=0, inwork_by_stuffid=sid, can_not_be_performed=0,
                     performingdate=ZERO_DATE)
        elif code == 4:  # performed
            p.update(closingdate=ZERO_DATE, performed_by_stuffid=sid, closed_by_stuffid=0,
                     performed_without_on_site_visit=0, inwork_by_stuffid=0, can_not_be_performed=0)
        else:  # closed
            p.update(closed_by_stuffid=sid)
        return {k: _form_value(v) for k, v in p.items()}

    async def _stuffid(self) -> int:
        """The session's own staff id (data.stuff_propers.stuffid), fetched once. Status changes send it."""
        if self._stuff_id is None:
            env = await self.client.call(STUFF_URI)
            raw = env.get("data")
            data: dict[str, Any] = raw if isinstance(raw, dict) else {}
            stuff = data.get("stuff_propers") if isinstance(data.get("stuff_propers"), dict) else data
            sid = stuff.get("stuffid") if isinstance(stuff, dict) else None
            if sid in (None, ""):
                raise MikbillError("upstream_error", "could not determine the acting staff id")
            self._stuff_id = int(sid)
        return self._stuff_id

    async def _ticket_states(self, ids: list[int]) -> list[dict[str, Any]]:
        env = await self.client.call(TICKET_LIST_URI, {}, no_success_key=True)
        rows = {str(r.get("ticketid")): r for r in (env.get("data") or []) if isinstance(r, dict)}
        out = []
        for tid in ids:
            row = rows.get(str(tid))
            out.append({"ticketid": tid, **{k: row.get(k) for k in TICKET_STATE_KEYS}} if row
                       else {"ticketid": tid, "listed": False})
        return out

    # ---- dry run -------------------------------------------------------------------------

    async def _dry_run(self, ep: Endpoint, schema: ToolSchema, args: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        warnings: list[str] = []
        uri = ep.uri
        if ep.tool == "mb_restore_user":
            uri, params, before = await self._restore_plan(args)
            summary["targets"] = [before]
            summary["count"] = 1
        else:
            params = await self._params(ep, schema, args)
        if ep.tool in WARNINGS:
            warnings.append(WARNINGS[ep.tool])
        if ep.status in ("blocked", "denied"):
            warnings.append(f"This action is '{ep.status}' on the reference installation and may return an error.")
        if ep.tool in USER_TARGET_TOOLS:
            uids = args["uids"] if schema.uids else [args["uid"]]
            summary["targets"] = await self._user_states(uids)
            summary["count"] = len(uids)
        if ep.tool in TICKET_TARGET_TOOLS and "ticket_ids" in args:
            summary["targets"] = await self._ticket_states(args["ticket_ids"])
            summary["count"] = len(args["ticket_ids"])
        if ep.tool in PAYMENT_TYPE_TOOLS:
            for t in await self._payment_types():
                if t["bughtypeid"] == args["bughtypeid"]:
                    summary["type"] = {"bughtypeid": t["bughtypeid"], "typename": t["typename"], "kind": t["kind"]}
        if ep.tool in ("mb_create_payment", "mb_bulk_payment", "mb_receive_payment"):
            count = len(args["uids"]) if schema.uids else 1
            try:
                summary["total_amount"] = float(args["deposit"]) * count
            except (TypeError, ValueError):
                warnings.append("deposit is not numeric; total could not be computed")
        if ep.tool in CHECKS:
            uri, keys = CHECKS[ep.tool]
            try:
                await self.client.call(uri, {k: params[k] for k in keys if k in params})
                summary["check"] = {"uri": uri, "ok": True}
            except MikbillError as err:
                summary["check"] = {"uri": uri, "ok": False, "error": err.to_dict()}
        out: dict[str, Any] = {
            "tool": ep.tool,
            "scope": ep.scope,
            "request": {"method": "POST", "uri": uri, "params": redact(params)},
            "summary": summary,
        }
        if warnings:
            out["warnings"] = warnings
        return out

    async def _payment_types(self) -> list[dict[str, Any]]:
        """The operation types of all four UI lists as {bughtypeid, typename, kind}, cached for a few minutes."""
        now = time.monotonic()
        if self._pay_types is not None and now - self._pay_types[0] < PAY_TYPES_TTL:
            return self._pay_types[1]
        rows: list[dict[str, Any]] = []
        for kind in PAYMENT_TYPE_KINDS:
            env = await self.client.call(PAY_TYPE_URIS[kind], {})
            for row in env.get("data") or []:
                try:
                    type_id = int(str(row.get("bughtypeid")))
                except (AttributeError, TypeError, ValueError):
                    continue
                if type_id >= 0:  # -1 is "set balance"; the tools take ids of 0 or more
                    rows.append({"bughtypeid": type_id, "typename": str(row.get("typename") or ""), "kind": kind})
        self._pay_types = (now, rows)
        return rows

    async def _check_payment_type(self, type_id: Any) -> None:
        """Refuse a bughtypeid that is not one of the installation's operation types."""
        known = await self._payment_types()
        if not any(t["bughtypeid"] == type_id for t in known):
            options = ", ".join(f"{t['bughtypeid']} {t['typename']}" for t in known[:40]) or "none found"
            raise MikbillError("validation", f"bughtypeid {type_id} is not a payment type on this installation "
                                             f"(Mikbill would record a payment with no type). Use one from "
                                             f"mb_list_payment_types: {options}")

    async def _user_states(self, uids: list[int]) -> list[dict[str, Any]]:
        states = []
        for uid in uids:
            try:
                env = await self.client.call(USER_URI, {"uid": str(uid)})
                raw = env.get("data")
                data: dict[str, Any] = raw if isinstance(raw, dict) else {}
                states.append({"uid": uid, **{k: data.get(k) for k in USER_STATE_KEYS}})
            except MikbillError as err:
                states.append({"uid": uid, "error": err.to_dict()})
        return states

    # ---- run -----------------------------------------------------------------------------

    async def _restore_plan(self, args: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Which request restores the user: enableusersmulti (same tariff) or vkluserflex (assign a tariff).

        Both were verified on the demo to turn a soft-deleted user (state 4) back into state 1.
        """
        uid = args["uid"]
        before = (await self._user_states([uid]))[0]
        if "error" in before:
            raise MikbillError(before["error"]["code"], before["error"]["message"])
        if str(before.get("state")) != "4" and str(before.get("deletedtable")) != "1":
            raise MikbillError("precondition_failed",
                               f"user {uid} is not deleted (state {before.get('state')}, status {before.get('status')})")
        if "gid" in args:
            return VKL_URI, {"uid": str(uid), "gid": str(args["gid"]), "itog_summa": str(args.get("itog_summa", 0)),
                             "type_list": "2"}, before
        if "itog_summa" in args:
            raise MikbillError("validation", "itog_summa is only used together with gid")
        return ENABLE_USERS_URI, {"set_users_list": str(uid)}, before

    async def _restore(self, ep: Endpoint, args: dict[str, Any], *, reveal_identity: bool) -> dict[str, Any]:
        uri, params, before = await self._restore_plan(args)
        await self._send(ep, uri, params)
        after = (await self._user_states([args["uid"]]))[0]
        if str(after.get("state")) == "4":
            raise MikbillError("upstream_error", "Mikbill reported success but the user is still deleted")
        method = "vkluserflex" if uri == VKL_URI else "enableusersmulti"
        return _ok({"restored": True, "method": method},
                   **redact_output({"state_before": [before], "state_after": [after]}, reveal_identity=reveal_identity))

    async def _send(self, ep: Endpoint, uri: str, params: dict[str, str], **kw: Any) -> dict[str, Any]:
        """The call that does the tool's work. When a write fails after the request left, the outcome is unknown and
        the error says so, instead of looking like a clean failure that is safe to retry."""
        try:
            return await self.client.call(uri, params, **kw)
        except MikbillError as err:
            if err.maybe_applied and not ep.is_read:
                err.mark_outcome_unknown()
            raise

    async def _enforce_check(self, ep: Endpoint, schema: ToolSchema, args: dict[str, Any]) -> None:
        """Run the dependency check before a delete that Mikbill would carry out regardless, and refuse on failure."""
        uri, keys = CHECKS[ep.tool]
        params = self.build_params(schema, args)
        try:
            await self.client.call(uri, {k: params[k] for k in keys if k in params})
        except MikbillError as err:
            if err.code in ("timeout", "auth_failed"):
                raise  # the check did not run; nothing was deleted
            raise MikbillError("conflict", f"the dependency check failed ({err.message}). Mikbill would delete it "
                                           "anyway and leave the dependent records pointing at nothing. Nothing was "
                                           "deleted. Pass force true only if the user wants that.") from err

    async def _run(self, ep: Endpoint, schema: ToolSchema, args: dict[str, Any], *, force: bool = False,
                   reveal_identity: bool = False) -> dict[str, Any]:
        if ep.tool == "mb_restore_user":
            return await self._restore(ep, args, reveal_identity=reveal_identity)
        if ep.tool == "mb_list_payment_types":
            types = [t for t in await self._payment_types() if args.get("kind") in (None, t["kind"])]
            return _ok(types, totalCount=len(types))
        if ep.tool in CHECKS and not force:
            await self._enforce_check(ep, schema, args)
        filters = {k: args[k] for k in schema.row_filters if k in args}
        limit = args.get("limit", schema.row_limit)
        params = await self._params(ep, schema, {k: v for k, v in args.items()
                                                 if k not in filters and k not in ("limit", "offset", "detail")})
        env = await self._send(ep, ep.uri, params, allow_empty=schema.allow_empty,
                               no_success_key=schema.no_success_key)
        if filters:  # Mikbill ignores filter parameters for this endpoint, so filter the rows here
            rows = [r for r in (env.get("data") or []) if isinstance(r, dict)
                    and all(str(r.get(k)) == str(v) for k, v in filters.items())]
            env = {**env, "data": rows}
        extra: dict[str, Any] = {}
        if limit and isinstance(env.get("data"), list):
            rows = env["data"]
            offset = args.get("offset", 0)
            # totalCount is the number of rows that matched, before limit and offset (not Mikbill's unfiltered count)
            env = {**env, "data": rows[offset:offset + limit], "totalCount": len(rows)}
            extra["truncated"] = offset + limit < len(rows)
            if extra["truncated"]:
                extra["next_offset"] = offset + limit
            if offset:
                extra["offset"] = offset
        if schema.summary and args.get("detail", "summary") != "full" and isinstance(env.get("data"), list):
            # Data minimisation: a list of subscribers returns a short row unless the caller asks for the record
            env = {**env, "data": [{k: r[k] for k in schema.summary if k in r} if isinstance(r, dict) else r
                                   for r in env["data"]]}
            extra["detail"] = "summary"
        if ep.tool in READ_BACK_TOOLS:
            uids = args["uids"] if schema.uids else [args["uid"]]
            extra["state_after"] = await self._user_states(uids)
        if ep.tool in TICKET_TARGET_TOOLS:
            extra["state_after"] = await self._ticket_states(args["ticket_ids"])
        return self._shape(ep, env, extra, reveal_identity=reveal_identity)

    @staticmethod
    def _shape(ep: Endpoint, env: dict[str, Any], extra: dict[str, Any], *,
               reveal_identity: bool = False) -> dict[str, Any]:
        if ep.tool == "mb_check_session":
            return _ok({"logged_in": True})
        data = env.get("data")
        if data is None:
            data = {k: v for k, v in env.items() if k not in ("success", "error")}
        extras = dict(extra)
        if "totalCount" in env:
            extras["totalCount"] = env["totalCount"]
        if "counters" in env:  # ticket list: status, priority and category counters (unfiltered)
            extras["counters"] = env["counters"]
        return _ok(redact_output(data, reveal_identity=reveal_identity),
                   **redact_output(extras, reveal_identity=reveal_identity))


_INT_RE = re.compile(r"-?\d{1,15}", re.ASCII)
_AMOUNT_RE = re.compile(r"-?\d{1,12}(\.\d{1,4})?", re.ASCII)


def _minimum(schema: ToolSchema, name: str) -> int:
    """Smallest value of a whole-number argument: 1 for the id of the record a call acts on, else 0."""
    return 1 if name in TARGET_ID_FIELDS and name in schema.required else 0


def _whole_number(name: str, value: Any, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """An integer argument. A string of digits is accepted; anything else (1.5, true, "1,2", "12abc") is refused, since
    Mikbill reads such values leniently and would act on something else."""
    if isinstance(value, bool):
        number = None
    elif isinstance(value, int):
        number = value
    elif isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
        number = int(value.strip())
    else:
        number = None
    if number is None:
        raise MikbillError("validation", f"{name} must be a whole number")
    if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        if maximum is None:
            raise MikbillError("validation", f"{name} must be {minimum} or more")
        raise MikbillError("validation", f"{name} must be between {minimum} and {maximum}")
    return number


def _amount(name: str, value: Any, *, positive: bool = False, maximum: float | None = None) -> Any:
    """A money argument: a plain decimal (no exponent, no NaN or infinity, at most 4 decimals), 0 or more, or more
    than 0 and capped for amounts that are credited to an account. The value is returned as it was given."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise MikbillError("validation", f"{name} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise MikbillError("validation", f"{name} must be a finite number")
    text = value.strip() if isinstance(value, str) else repr(value)
    if not _AMOUNT_RE.fullmatch(text):
        raise MikbillError("validation", f"{name} must be a plain decimal number such as 150 or 99.50")
    amount = Decimal(text)
    if amount < 0 or (positive and amount == 0):
        raise MikbillError("validation", f"{name} must be {'more than 0' if positive else '0 or more'}")
    if maximum is not None and amount > Decimal(str(maximum)):
        raise MikbillError("validation", f"{name} {amount} is above the limit of {maximum:g} per account "
                                         "(MB_MAX_PAYMENT)")
    return text if isinstance(value, str) else value


def _flag(name: str, value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str) and value.strip() in ("0", "1"):
        return int(value.strip())
    raise MikbillError("validation", f"{name} must be 0 or 1")


def _date(name: str, value: Any) -> str:
    """A calendar date as YYYY-MM-DD."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            datetime.date.fromisoformat(value.strip())
            return value.strip()
        except ValueError:
            pass
    raise MikbillError("validation", f"{name} must be a date as YYYY-MM-DD")


def _scalar(name: str, value: Any) -> Any:
    """A text or untyped argument: a string of bounded length, a finite number or a boolean."""
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MikbillError("validation", f"{name} must be a finite number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT_INPUT:
            raise MikbillError("validation", f"{name} is too long (limit {MAX_TEXT_INPUT} characters)")
        return value
    raise MikbillError("validation", f"{name} must be a string, number or boolean")


def _form_value(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def to_text(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str)
