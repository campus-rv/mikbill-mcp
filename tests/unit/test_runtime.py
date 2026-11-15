import json

import pytest

from conftest import ALL_SCOPE_SET, FakeClient
from mb_mcp.endpoints import ALL
from mb_mcp.errors import MikbillError
from mb_mcp.runtime import Runtime

READ_WRITE = frozenset({"read", "write"})


def names(runtime, scopes):
    return {ep.tool for ep in runtime.tools_for(scopes)}


# ---- scopes and listing ---------------------------------------------------------------------

def test_listing_respects_scopes(runtime):
    assert len(names(runtime, READ_WRITE)) == 39
    assert "mb_add_settlement" not in names(runtime, READ_WRITE)  # address objects are admin
    assert "mb_add_house" not in names(runtime, READ_WRITE)
    assert "mb_add_house" in names(runtime, frozenset({"read", "admin"}))
    assert "mb_delete_settlement" not in names(runtime, READ_WRITE)
    assert "mb_add_switch" not in names(runtime, READ_WRITE)
    assert "mb_add_switch" in names(runtime, frozenset({"read", "admin"}))
    assert len(names(runtime, ALL_SCOPE_SET)) == 95
    assert "mb_delete_user" not in names(runtime, READ_WRITE)  # soft delete is gated account
    assert "mb_delete_user" in names(runtime, frozenset({"read", "account"}))
    assert "mb_bulk_delete_users" not in names(runtime, READ_WRITE)
    assert "mb_create_payment" not in names(runtime, frozenset({"read", "write", "account"}))


def test_listing_is_limited_by_server_scopes(settings):
    from dataclasses import replace
    rt = Runtime(replace(settings, scopes=frozenset({"read"})), FakeClient())
    assert len(rt.tools_for(ALL_SCOPE_SET)) == 26


def test_every_tool_has_a_valid_input_schema(runtime):
    for ep in ALL:
        d = runtime.describe(ep)
        s = d["inputSchema"]
        assert s["type"] == "object" and s["additionalProperties"] is False
        assert set(s["required"]) <= set(s["properties"]), ep.tool
        assert ("dry_run" in s["properties"]) == ep.gated
        assert ("reason" in s["required"]) == ep.gated
        assert d["description"]


async def test_scope_denied_for_ungranted_tool(runtime, fake):
    r = await runtime.execute("mb_bulk_delete_users", {"uids": [1], "reason": "test"}, READ_WRITE)
    assert r["ok"] is False and r["error"]["code"] == "scope_denied"
    assert fake.calls == []


async def test_unknown_tool(runtime):
    r = await runtime.execute("mb_nope", {}, ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation"


# ---- validation -----------------------------------------------------------------------------

async def test_unknown_and_missing_parameters(runtime, fake):
    r = await runtime.execute("mb_get_user", {"uid": 1, "bogus": 2}, READ_WRITE)
    assert r["error"]["code"] == "validation" and "bogus" in r["error"]["message"]
    r = await runtime.execute("mb_get_user", {}, READ_WRITE)
    assert r["error"]["code"] == "validation" and "uid" in r["error"]["message"]
    assert fake.calls == []


async def test_search_users_requires_a_filter(runtime):
    r = await runtime.execute("mb_search_users", {}, READ_WRITE)
    assert r["error"]["code"] == "validation"


async def test_values_must_be_scalars(runtime):
    r = await runtime.execute("mb_get_user", {"uid": [1]}, READ_WRITE)
    assert r["error"]["code"] == "validation"


async def test_dry_run_flag_is_not_accepted_on_ungated_tools(runtime):
    r = await runtime.execute("mb_get_user", {"uid": 1, "dry_run": True}, READ_WRITE)
    assert r["error"]["code"] == "validation"


async def test_bulk_uid_validation(runtime):
    for bad in ([], [1, 1], [1, "2"], list(range(6)), "1,2"):
        r = await runtime.execute("mb_bulk_disable_users", {"uids": bad, "reason": "test"}, ALL_SCOPE_SET)
        assert r["error"]["code"] == "validation", bad


# ---- read tools -----------------------------------------------------------------------------

async def test_read_tool_returns_data_and_redacts_credentials(runtime, fake):
    fake.responses["/json/users/getuserdatambp"] = {
        "success": True, "totalCount": 0,
        "data": {"user": "u1", "password": "plain", "router_pass": "rp", "fio": "Name"}}
    r = await runtime.execute("mb_get_user", {"uid": 1}, READ_WRITE)
    assert r["ok"] is True
    assert r["data"]["password"] != "plain" and r["data"]["router_pass"] != "rp"
    assert r["data"]["user"] == "u1"
    assert fake.posted("/json/users/getuserdatambp") == [{"uid": "1"}]


async def test_check_session(runtime, fake):
    r = await runtime.execute("mb_check_session", {}, READ_WRITE)
    assert r["data"] == {"logged_in": True}
    assert fake.calls[0][0] == "/json/users/getcountfl"


async def test_upstream_error_becomes_an_error_envelope(runtime, fake):
    fake.responses["/json/users/getuserdatambp"] = MikbillError("auth_failed", "no")
    r = await runtime.execute("mb_get_user", {"uid": 1}, READ_WRITE)
    assert r == {"ok": False, "error": {"code": "auth_failed", "message": "no"}}


# ---- writes (not gated) ---------------------------------------------------------------------

async def test_write_applies_defaults_and_encodes_values(runtime, fake):
    fake.responses["/extjs/users/createabonent"] = {"success": True, "data": {"useruid": 9, "user": "u"}}
    args = {"user": "u", "password": "p", "fio": "F", "gid": 1, "sectorid": 13, "settlementid": 1,
            "neighborhoodid": 2, "laneid": 3, "houseid": 4, "app": "1"}
    r = await runtime.execute("mb_create_user", args, READ_WRITE)
    assert r["ok"]
    sent = fake.posted("/extjs/users/createabonent")[0]
    assert sent["use_installments"] == "0" and sent["lane_create"] == "0" and sent["phone"] == ""
    assert sent["gid"] == "1"


async def test_kick_user_is_not_gated(runtime, fake):
    r = await runtime.execute("mb_kick_user", {"uid": 5}, READ_WRITE)
    assert r["ok"] and fake.posted("/json/users/userkickfl") == [{"uid": "5"}]


# ---- gated: reason, bulk preview, no approval token -----------------------------------------
# The client (harness) asks the user to approve a call. The server needs the scope and a reason, and previews bulk tools
# by default.

def gated(args, **extra):
    return {**args, "reason": "unit test", **extra}


PAYMENT = {"uid": 1, "bughtypeid": 17, "deposit": 10, "prim2": "n"}


async def _run_gated(runtime, tool, args):
    """A gated call that executes, whether or not the tool previews by default."""
    return await runtime.execute(tool, gated(args, dry_run=False), ALL_SCOPE_SET)


async def test_a_single_gated_tool_executes_on_the_first_call(runtime, fake):
    r = await runtime.execute("mb_create_payment", gated(PAYMENT), ALL_SCOPE_SET)
    assert r["ok"] and "dry_run" not in r
    assert fake.posted("/json/users/paymentdoflex") == [{"uid": "1", "bughtypeid": "17", "deposit": "10", "prim2": "n"}]


async def test_dry_run_true_previews_a_single_tool_and_makes_no_change(runtime, fake):
    r = await runtime.execute("mb_create_payment", gated(PAYMENT, dry_run=True), ALL_SCOPE_SET)
    assert r["ok"] and r["dry_run"] is True
    assert r["data"]["summary"]["total_amount"] == 10.0
    assert "approval_token" not in r["data"] and "expires_in_seconds" not in r["data"]
    assert fake.posted("/json/users/paymentdoflex") == []


async def test_bulk_tools_preview_by_default_and_execute_with_dry_run_false(runtime, fake):
    args = {"uids": [10, 11]}
    preview = await runtime.execute("mb_bulk_unfreeze_users", gated(args), ALL_SCOPE_SET)
    assert preview["ok"] and preview["dry_run"] is True and "approval_token" not in preview["data"]
    assert fake.posted("/json/users/unfreezeusersmulti") == []
    done = await runtime.execute("mb_bulk_unfreeze_users", gated(args, dry_run=False), ALL_SCOPE_SET)
    assert done["ok"] and "dry_run" not in done
    assert fake.posted("/json/users/unfreezeusersmulti") == [{"set_users_list": "10,11"}]


@pytest.mark.parametrize("tool", [ep.tool for ep in ALL if ep.gated])
def test_only_tools_that_take_uids_preview_by_default(runtime, tool):
    ep = next(e for e in ALL if e.tool == tool)
    prop = runtime.describe(ep)["inputSchema"]["properties"]["dry_run"]
    assert prop["default"] is (tool.startswith("mb_bulk_")), tool


async def test_approval_tokens_no_longer_exist(runtime, fake):
    r = await runtime.execute("mb_create_payment", gated(PAYMENT, approval_token="x"), ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation" and "approval_token" in r["error"]["message"]
    assert fake.posted("/json/users/paymentdoflex") == []


@pytest.mark.parametrize("value", ["false", 0, None, "no"])
async def test_dry_run_must_be_a_boolean(runtime, fake, value):
    r = await runtime.execute("mb_create_payment", gated(PAYMENT, dry_run=value), ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation" and "dry_run" in r["error"]["message"]
    assert fake.calls == []


@pytest.mark.parametrize("reason", [None, "", "no"])
async def test_reason_is_required_on_gated_calls(runtime, reason):
    args = {"uids": [1]}
    if reason is not None:
        args["reason"] = reason
    r = await runtime.execute("mb_bulk_delete_users", args, ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation" and "reason" in r["error"]["message"]


async def test_bulk_preview_lists_every_uid_and_total(runtime, fake):
    fake.responses["/json/users/getuserdatambp"] = lambda p: {
        "success": True, "data": {"useruid": int(p["uid"]), "user": "u" + p["uid"], "state": "1",
                                  "status": "normal", "deletedtable": 0, "password": "plain"}}
    r = await runtime.execute("mb_bulk_payment", gated({"uids": [10, 11, 12], "bughtypeid": 17, "deposit": 5, "prim2": "x"}),
                              ALL_SCOPE_SET)
    s = r["data"]["summary"]
    assert [t["uid"] for t in s["targets"]] == [10, 11, 12]
    assert s["count"] == 3
    assert s["total_amount"] == 15.0
    assert "plain" not in json.dumps(r)
    assert r["data"]["request"]["params"]["set_users_list"] == "10,11,12"
    assert fake.posted("/json/users/paymentdoflexmulti") == [] and not any("payment" in u for u, _ in fake.calls if "getuser" not in u)


async def test_bulk_user_state_preview_never_leaks_passwords(runtime, fake):
    fake.responses["/json/users/getuserdatambp"] = lambda p: {
        "success": True, "data": {"useruid": int(p["uid"]), "user": "u", "state": "1", "status": "normal",
                                  "deletedtable": 0, "password": "plain"}}
    r = await runtime.execute("mb_bulk_delete_users", gated({"uids": [10, 11]}), ALL_SCOPE_SET)
    targets = r["data"]["summary"]["targets"]
    assert [t["uid"] for t in targets] == [10, 11]
    assert "plain" not in json.dumps(r)
    assert r["data"]["summary"]["count"] == 2
    assert any("Soft delete" in w for w in r["data"]["warnings"])


async def test_soft_delete_reads_back_the_state(runtime, fake):
    fake.responses["/json/users/getuserdatambp"] = {"success": True, "data": {"useruid": 7, "user": "u", "state": "4",
                                                                              "status": "deleted", "deletedtable": 1}}
    r = await _run_gated(runtime, "mb_delete_user", {"uid": 7})
    assert r["ok"]
    assert r["state_after"][0]["status"] == "deleted" and r["state_after"][0]["deletedtable"] == 1
    assert fake.posted("/json/users/deluserflex") == [{"uid": "7"}]


async def test_soft_delete_user_can_be_previewed(runtime, fake):
    dry = await runtime.execute("mb_delete_user", gated({"uid": 7}, dry_run=True), ALL_SCOPE_SET)
    assert dry["dry_run"] is True and dry["data"]["summary"]["count"] == 1
    assert any("Soft delete" in w for w in dry["data"]["warnings"])
    assert fake.posted("/json/users/deluserflex") == []


async def test_bulk_soft_delete_previews_first_then_executes(runtime, fake):
    first = await runtime.execute("mb_bulk_delete_users", gated({"uids": [7, 8]}), ALL_SCOPE_SET)
    assert first["dry_run"] is True and fake.posted("/json/users/deleteusersmulti") == []
    r = await _run_gated(runtime, "mb_bulk_delete_users", {"uids": [7, 8]})
    assert r["ok"] and fake.posted("/json/users/deleteusersmulti") == [{"set_users_list": "7,8"}]


async def test_lane_and_neighborhood_previews_run_the_dependency_checks(runtime, fake):
    fake.responses["/extjs/options/checkdellane"] = MikbillError("upstream_error", "can't delete lane with houses")
    r = await runtime.execute("mb_delete_lane", gated({"laneid": 41}, dry_run=True), ALL_SCOPE_SET)
    assert r["data"]["summary"]["check"]["ok"] is False
    assert fake.posted("/json/options/dellaneflex") == []
    r = await runtime.execute("mb_delete_neighborhood", gated({"neighborhoodid": 20}, dry_run=True), ALL_SCOPE_SET)
    assert r["data"]["summary"]["check"] == {"uri": "/json/options/checkdeleteneighborhood", "ok": True}


async def test_delete_house_sends_houseid_never_delete_list(runtime, fake):
    await _run_gated(runtime, "mb_delete_house", {"houseid": 92})
    assert fake.posted("/json/options/delhouseflex") == [{"houseid": "92"}]


async def test_delete_house_conflict_is_reported(runtime, fake):
    fake.responses["/json/options/delhouseflex"] = MikbillError("conflict", "in use")
    r = await _run_gated(runtime, "mb_delete_house", {"houseid": 92})
    assert r["error"]["code"] == "conflict"


async def test_delete_user_group_sends_delete_list(runtime, fake):
    r = await runtime.execute("mb_delete_user_group", gated({"usersgroupid": 5}, dry_run=True), ALL_SCOPE_SET)
    assert any("not checked" in w for w in r["data"]["warnings"])
    await _run_gated(runtime, "mb_delete_user_group", {"usersgroupid": 5})
    assert fake.posted("/json/options/deleteusersgroup") == [{"delete_list": "5"}]


async def test_fixed_and_default_params(runtime, fake):
    await _run_gated(runtime, "mb_enable_flex_tariff", {"uid": 3, "gid": 49})
    assert fake.posted("/json/users/vkluserflex") == [{"uid": "3", "gid": "49", "itog_summa": "0", "type_list": "2"}]
    await _run_gated(runtime, "mb_delete_disconnected_user", {"uid": 3, "gid": 49})
    assert fake.posted("/json/users/deluserotklflex") == [{"uid": "3", "gid": "49", "itog_summa": "0", "type_list": "2"}]


async def test_delete_check_endpoint_runs_during_the_preview(runtime, fake):
    r = await runtime.execute("mb_delete_settlement", gated({"settlementid": 16}, dry_run=True), ALL_SCOPE_SET)
    assert r["data"]["summary"]["check"] == {"uri": "/json/options/checkdeletesettlement", "ok": True}
    assert fake.posted("/json/options/deletesettlement") == []


async def test_blocked_tools_warn_in_the_preview(runtime):
    r = await runtime.execute("mb_set_personal_service", gated({"uid": 1, "serviceid": 115}, dry_run=True), ALL_SCOPE_SET)
    assert r["data"]["warnings"]


async def test_failed_gated_call_returns_the_upstream_error(runtime, fake):
    fake.responses["/json/users/paymentdoflex"] = MikbillError("upstream_error", "boom")
    args = {"uid": 1, "bughtypeid": 17, "deposit": 1, "prim2": "n"}
    r = await _run_gated(runtime, "mb_create_payment", args)
    assert r["error"]["code"] == "upstream_error"


async def test_executed_gated_calls_are_audited_with_the_reason_and_previews_are_not(settings, fake, tmp_path):
    import json as _json
    from mb_mcp.audit import AuditLog
    path = tmp_path / "audit.log"
    rt = Runtime(settings, fake, AuditLog(str(path)))
    await rt.execute("mb_bulk_unfreeze_users", {"uids": [1], "reason": "preview only"}, ALL_SCOPE_SET, principal="agent1")
    assert not path.exists() or path.read_text(encoding="utf-8") == ""
    await rt.execute("mb_bulk_unfreeze_users", {"uids": [1], "reason": "customer asked", "dry_run": False},
                     ALL_SCOPE_SET, principal="agent1")
    events = [_json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [(e["tool"], e["outcome"], e["reason"], e["principal"]) for e in events] == [
        ("mb_bulk_unfreeze_users", "ok", "customer asked", "agent1")]


async def test_audit_records_do_not_contain_personal_data(settings, fake, tmp_path):
    from dataclasses import replace
    from mb_mcp.audit import AuditLog
    path = tmp_path / "audit.log"
    rt = Runtime(replace(settings), fake, AuditLog(str(path)))
    await rt.execute("mb_create_user", {"user": "u", "password": "topsecret", "fio": "Real Name", "gid": 1,
                                        "sectorid": 1, "settlementid": 1, "neighborhoodid": 1, "laneid": 1,
                                        "houseid": 1, "app": "1", "phone": "+380995551234", "email": "a@b.c"},
                     READ_WRITE, principal="agent1")
    text = path.read_text(encoding="utf-8")
    assert "topsecret" not in text and "Real Name" not in text and "995551234" not in text and "a@b.c" not in text
    assert "mb_create_user" in text and "agent1" in text
