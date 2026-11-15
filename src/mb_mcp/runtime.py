"""Transport-independent tool runtime: validation, scopes, dry-run, approval, execution, shaping.

The MCP server (server.py) only translates between MCP messages and Runtime.execute().
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from .audit import AuditLog
from .config import Settings
from .endpoints import ALL, Endpoint
from .errors import MikbillError
from .gating import ApprovalStore
from .redaction import redact
from .schemas import SCALAR_TYPES, SCHEMAS, ToolSchema

USER_URI = "/json/users/getuserdatambp"
USER_STATE_KEYS = ("useruid", "user", "state", "status", "deletedtable")

ENABLE_USERS_URI = "/json/users/enableusersmulti"
VKL_URI = "/json/users/vkluserflex"
DEVICES_URI = "/json/users/getuserdevicesbyuid"
DEVICE_KEYS = ("devid", "devtypeid", "devtypename", "enabled", "cost", "rent", "sell_day")

TICKET_LIST_URI = "/extjs/ticketview/getticketslist2x"
STUFF_URI = "/json/index/getstuffproper"
TICKET_STATE_KEYS = ("statustypeid", "categoryid", "prioritytypeid", "rating", "creationdate")
TICKET_TARGET_TOOLS = {"mb_set_ticket_status", "mb_delete_tickets"}
STATUS_CODES = {"open": 1, "closed": 2, "in_work": 3, "performed": 4}
ZERO_DATE = "0000-00-00 00:00:00"

# Delete tools whose targets are users: the dry-run and the result report each user's state.
USER_TARGET_TOOLS = {
    "mb_delete_user", "mb_bulk_delete_users", "mb_delete_disconnected_user", "mb_delete_user_full",
    "mb_disconnect_user", "mb_freeze_user", "mb_bulk_disable_users", "mb_bulk_enable_users",
    "mb_bulk_unfreeze_users", "mb_bulk_enable_internet",
    "mb_bulk_payment", "mb_bulk_issue_credit", "mb_bulk_remove_credit",
}
READ_BACK_TOOLS = {"mb_delete_user", "mb_bulk_delete_users", "mb_delete_disconnected_user", "mb_disconnect_user"}

# Check endpoints run during dry-run: tool -> (uri, parameters to send)
CHECKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "mb_delete_settlement": ("/json/options/checkdeletesettlement", ("settlementid",)),
    # Mikbill's delete endpoints for these do NOT enforce the check (verified on the demo: a lane, neighborhood and
    # settlement were deleted while a house still pointed at them), so the dry-run is where dependents show up.
    "mb_delete_lane": ("/extjs/options/checkdellane", ("laneid",)),
    "mb_delete_neighborhood": ("/json/options/checkdeleteneighborhood", ("neighborhoodid",)),
    "mb_delete_packet": ("/json/tarif/delchecktarifflex", ("gid",)),
}

WARNINGS: dict[str, str] = {
    "mb_delete_house": "Sent as houseid so Mikbill refuses (error 8) while users reference the house. The delete_list "
                       "form reports success without deleting and is never used.",
    "mb_delete_user_group": "The number of users in the group is not checked: no verified filter exists. Deleting a "
                            "group that users reference may orphan them.",
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
    "mb_delete_dev_type": "Sent as delete_list: a plain id makes Mikbill answer success without deleting. A device "
                          "type that a device still uses is refused.",
    "mb_delete_tickets": "Deletes the tickets listed in the preview. This cannot be undone.",
    "mb_delete_ticket_note": "Deletes one ticket note. This cannot be undone.",
}


class ClientLike(Protocol):
    async def call(self, uri: str, params: dict[str, Any] | None = None, *, allow_empty: bool = False,
                   no_success_key: bool = False) -> dict[str, Any]:
        ...


def _ok(data: Any, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "status": 200, "data": data, **extra}


def _fail(err: MikbillError) -> dict[str, Any]:
    return {"ok": False, "error": err.to_dict()}


class Runtime:
    def __init__(self, settings: Settings, client: ClientLike, approvals: ApprovalStore | None = None,
                 audit: AuditLog | None = None) -> None:
        self.settings = settings
        self.client = client
        self.approvals = approvals or ApprovalStore()
        self.audit = audit or AuditLog(settings.audit_file)
        self._by_name = {ep.tool: ep for ep in ALL}
        self._stuff_id: int | None = None  # the session's own staff id, fetched once for ticket status changes

    # ---- listing -------------------------------------------------------------------------

    def tools_for(self, scopes: frozenset[str]) -> list[Endpoint]:
        allowed = scopes & self.settings.scopes
        return [ep for ep in ALL if ep.scope in allowed]

    def describe(self, ep: Endpoint) -> dict[str, Any]:
        """MCP tool definition: name, description, inputSchema."""
        schema = SCHEMAS[ep.tool]
        props: dict[str, Any] = {f: {"type": SCALAR_TYPES} for f in schema.fields}
        for key, options in schema.enums.items():
            props[key] = {"type": "string" if isinstance(options[0], str) else "integer", "enum": list(options)}
        required = list(schema.required)
        if schema.ticket_ids:
            props["ticket_ids"] = {"type": "array", "items": {"type": "integer"}, "minItems": 1,
                                   "maxItems": self.settings.max_bulk, "uniqueItems": True,
                                   "description": "Ticket ids."}
            required.append("ticket_ids")
        if schema.uids:
            props["uids"] = {"type": "array", "items": {"type": "integer"}, "minItems": 1,
                             "maxItems": self.settings.max_bulk, "uniqueItems": True,
                             "description": "Subscriber uids. Sent to Mikbill as set_users_list."}
            required.append("uids")
        if ep.gated:
            props["dry_run"] = {"type": "boolean", "default": True,
                                "description": "true (default) returns a preview and an approval_token; false executes."}
            props["approval_token"] = {"type": "string", "description": "Token from the dry-run. Required when dry_run is false."}
            props["reason"] = {"type": "string", "minLength": 3, "description": "Why this action is needed. Logged."}
            required.append("reason")
        return {
            "name": ep.tool,
            "description": self._description(ep),
            "inputSchema": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
        }

    def _description(self, ep: Endpoint) -> str:
        parts = [ep.note.rstrip(".") + "." if ep.note else ep.tool.replace("_", " ") + "."]
        if ep.tool in WARNINGS:
            parts.append(WARNINGS[ep.tool])
        if ep.gated:
            parts.append(f"Gated ({ep.scope}): call with dry_run true, then repeat with dry_run false, the "
                         f"approval_token and a reason.")
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
        if ep.scope not in (scopes & self.settings.scopes):
            self.audit.record(tool=name, scope=ep.scope, principal=principal, outcome="scope_denied")
            return _fail(MikbillError("scope_denied", f"scope '{ep.scope}' is not granted"))

        gated = ep.gated
        dry_run = bool(arguments.pop("dry_run", True)) if gated else False
        token = arguments.pop("approval_token", None) if gated else None
        reason = arguments.pop("reason", None) if gated else None
        try:
            schema = SCHEMAS[name]
            args = self._validate(schema, arguments)
            if gated and not (isinstance(reason, str) and len(reason.strip()) >= 3):
                raise MikbillError("validation", "reason is required (at least 3 characters)")

            if gated and dry_run:
                return _ok(await self._dry_run(ep, schema, args), dry_run=True)
            if gated and not self.approvals.consume(token, name, args):
                self.audit.record(tool=name, scope=ep.scope, principal=principal, outcome="approval_required",
                                  params=args, reason=reason)
                raise MikbillError("approval_required",
                                   "a valid, unused approval_token from a dry-run of the same parameters is required")
            result = await self._run(ep, schema, args)
        except MikbillError as err:
            if ep.scope != "read" and err.code not in ("validation", "approval_required"):
                self.audit.record(tool=name, scope=ep.scope, principal=principal, outcome=err.code,
                                  params=arguments, reason=reason, detail=err.message)
            return _fail(err)

        if ep.scope != "read":
            self.audit.record(tool=name, scope=ep.scope, principal=principal, outcome="ok", params=args, reason=reason)
        return result

    # ---- validation ----------------------------------------------------------------------

    def _validate(self, schema: ToolSchema, arguments: dict[str, Any]) -> dict[str, Any]:
        id_lists = ({"uids"} if schema.uids else set()) | ({"ticket_ids"} if schema.ticket_ids else set())
        allowed = set(schema.fields) | id_lists
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
            elif isinstance(value, (str, int, float, bool)):
                out[key] = value
            else:
                raise MikbillError("validation", f"{key} must be a string, number or boolean")
        for key, options in schema.enums.items():
            if key in out and (isinstance(out[key], bool) or out[key] not in options):
                raise MikbillError("validation", f"{key} must be one of: {', '.join(str(o) for o in options)}")
        return out

    def _validate_ids(self, name: str, value: Any) -> list[int]:
        if not isinstance(value, list) or not value or not all(isinstance(u, int) and not isinstance(u, bool) for u in value):
            raise MikbillError("validation", f"{name} must be a non-empty array of integers")
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
            data = env.get("data") if isinstance(env.get("data"), dict) else {}
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
        if ep.tool == "mb_delete_user_device":
            summary["targets"] = await self._device_targets(args["uid"], [args["devid"]])
        if ep.tool in WARNINGS:
            warnings.append(WARNINGS[ep.tool])
        if ep.status in ("blocked", "denied"):
            warnings.append(f"This action is '{ep.status}' on the reference installation and may return an error.")
        if ep.tool in TICKET_TARGET_TOOLS:
            summary["targets"] = await self._ticket_states(args["ticket_ids"])
            summary["count"] = len(args["ticket_ids"])
        if ep.tool in USER_TARGET_TOOLS:
            uids = args["uids"] if schema.uids else [args["uid"]]
            summary["targets"] = await self._user_states(uids)
            summary["count"] = len(uids)
        if ep.tool in ("mb_create_payment", "mb_bulk_payment"):
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
        token = self.approvals.issue(ep.tool, args)
        out: dict[str, Any] = {
            "tool": ep.tool,
            "scope": ep.scope,
            "request": {"method": "POST", "uri": uri, "params": redact(params)},
            "summary": summary,
            "approval_token": token,
            "expires_in_seconds": int(self.approvals.ttl),
        }
        if warnings:
            out["warnings"] = warnings
        return out

    async def _user_states(self, uids: list[int]) -> list[dict[str, Any]]:
        states = []
        for uid in uids:
            try:
                env = await self.client.call(USER_URI, {"uid": str(uid)})
                data = env.get("data") if isinstance(env.get("data"), dict) else {}
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

    async def _restore(self, args: dict[str, Any]) -> dict[str, Any]:
        uri, params, before = await self._restore_plan(args)
        await self.client.call(uri, params)
        after = (await self._user_states([args["uid"]]))[0]
        if str(after.get("state")) == "4":
            raise MikbillError("upstream_error", "Mikbill reported success but the user is still deleted")
        method = "vkluserflex" if uri == VKL_URI else "enableusersmulti"
        return _ok({"restored": True, "method": method}, state_before=[before], state_after=[after])

    async def _device_targets(self, uid: int, devids: list[int]) -> list[dict[str, Any]]:
        env = await self.client.call(DEVICES_URI, {"useruid": str(uid)}, allow_empty=True)
        rows = {str(r.get("devid")): r for r in (env.get("data") or []) if isinstance(r, dict)}
        return [{k: rows[str(d)].get(k) for k in DEVICE_KEYS} if str(d) in rows else {"devid": d, "listed": False}
                for d in devids]

    async def _run(self, ep: Endpoint, schema: ToolSchema, args: dict[str, Any]) -> dict[str, Any]:
        if ep.tool == "mb_restore_user":
            return await self._restore(args)
        filters = {k: args[k] for k in schema.row_filters if k in args}
        params = await self._params(ep, schema, {k: v for k, v in args.items() if k not in filters})
        env = await self.client.call(ep.uri, params, allow_empty=schema.allow_empty,
                                     no_success_key=schema.no_success_key)
        if filters:  # Mikbill ignores filter parameters for this endpoint, so filter the rows here
            rows = [r for r in (env.get("data") or []) if isinstance(r, dict)
                    and all(str(r.get(k)) == str(v) for k, v in filters.items())]
            env = {**env, "data": rows}
        extra: dict[str, Any] = {}
        if ep.tool in READ_BACK_TOOLS:
            uids = args["uids"] if schema.uids else [args["uid"]]
            extra["state_after"] = await self._user_states(uids)
        if ep.tool in TICKET_TARGET_TOOLS:
            extra["state_after"] = await self._ticket_states(args["ticket_ids"])
        return self._shape(ep, env, extra)

    @staticmethod
    def _shape(ep: Endpoint, env: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
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
        return _ok(redact(data), **redact(extras))


def _form_value(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def to_text(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str)
