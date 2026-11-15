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
    "mb_delete_packet": ("/json/tarif/delchecktarifflex", ("gid",)),
}

WARNINGS: dict[str, str] = {
    "mb_delete_house": "Sent as houseid so Mikbill refuses (error 8) while users reference the house. The delete_list "
                       "form skips that check and is never used.",
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
}


class ClientLike(Protocol):
    async def call(self, uri: str, params: dict[str, Any] | None = None, *, allow_empty: bool = False) -> dict[str, Any]:
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

    # ---- listing -------------------------------------------------------------------------

    def tools_for(self, scopes: frozenset[str]) -> list[Endpoint]:
        allowed = scopes & self.settings.scopes
        return [ep for ep in ALL if ep.scope in allowed]

    def describe(self, ep: Endpoint) -> dict[str, Any]:
        """MCP tool definition: name, description, inputSchema."""
        schema = SCHEMAS[ep.tool]
        props: dict[str, Any] = {f: {"type": SCALAR_TYPES} for f in schema.fields}
        required = list(schema.required)
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
        allowed = set(schema.fields) | ({"uids"} if schema.uids else set())
        unknown = set(arguments) - allowed
        if unknown:
            raise MikbillError("validation", f"unknown parameters: {', '.join(sorted(unknown))}")
        missing = [f for f in schema.required if arguments.get(f) in (None, "")]
        if schema.uids and not arguments.get("uids"):
            missing.append("uids")
        if missing:
            raise MikbillError("validation", f"missing required parameters: {', '.join(missing)}")
        out: dict[str, Any] = {}
        for key, value in arguments.items():
            if key == "uids":
                out[key] = self._validate_uids(value)
            elif isinstance(value, (str, int, float, bool)):
                out[key] = value
            else:
                raise MikbillError("validation", f"{key} must be a string, number or boolean")
        return out

    def _validate_uids(self, value: Any) -> list[int]:
        if not isinstance(value, list) or not value or not all(isinstance(u, int) and not isinstance(u, bool) for u in value):
            raise MikbillError("validation", "uids must be a non-empty array of integers")
        if len(set(value)) != len(value):
            raise MikbillError("validation", "uids must not contain duplicates")
        if len(value) > self.settings.max_bulk:
            raise MikbillError("validation", f"too many uids (limit {self.settings.max_bulk}, MB_MAX_BULK)")
        return list(value)

    # ---- request building ----------------------------------------------------------------

    @staticmethod
    def build_params(schema: ToolSchema, args: dict[str, Any]) -> dict[str, str]:
        """Form parameters sent to Mikbill: defaults, renames, fixed values, uids, string encoding."""
        merged: dict[str, Any] = {**schema.defaults, **args}
        uids = merged.pop("uids", None)
        out: dict[str, Any] = {schema.rename.get(k, k): v for k, v in merged.items()}
        out.update(schema.fixed)
        if uids is not None:
            out["set_users_list"] = ",".join(str(u) for u in uids)
        return {k: _form_value(v) for k, v in out.items()}

    # ---- dry run -------------------------------------------------------------------------

    async def _dry_run(self, ep: Endpoint, schema: ToolSchema, args: dict[str, Any]) -> dict[str, Any]:
        params = self.build_params(schema, args)
        summary: dict[str, Any] = {}
        warnings: list[str] = []
        if ep.tool in WARNINGS:
            warnings.append(WARNINGS[ep.tool])
        if ep.status in ("blocked", "denied"):
            warnings.append(f"This action is '{ep.status}' on the reference installation and may return an error.")
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
            "request": {"method": "POST", "uri": ep.uri, "params": redact(params)},
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

    async def _run(self, ep: Endpoint, schema: ToolSchema, args: dict[str, Any]) -> dict[str, Any]:
        params = self.build_params(schema, args)
        env = await self.client.call(ep.uri, params, allow_empty=schema.allow_empty)
        extra: dict[str, Any] = {}
        if ep.tool in READ_BACK_TOOLS:
            uids = args["uids"] if schema.uids else [args["uid"]]
            extra["state_after"] = await self._user_states(uids)
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
        return _ok(redact(data), **redact(extras))


def _form_value(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def to_text(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str)
