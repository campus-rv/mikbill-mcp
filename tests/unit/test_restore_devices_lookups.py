import pytest

from conftest import ALL_SCOPE_SET, FakeClient
from mb_mcp.endpoints import ALL
from mb_mcp.gating import ApprovalStore
from mb_mcp.runtime import Runtime

READ_WRITE = frozenset({"read", "write"})
USER = "/json/users/getuserdatambp"
ENABLE = "/json/users/enableusersmulti"
VKL = "/json/users/vkluserflex"
DEVICES = "/json/users/getuserdevicesbyuid"


def gated(args, **extra):
    return {**args, "reason": "unit test", **extra}


def user_row(state, status, deleted):
    return {"success": True, "data": {"useruid": 7, "user": "u7", "state": str(state), "status": status,
                                      "deletedtable": deleted, "password": "plain"}}


def sequence(*responses):
    """A fake response that returns the given envelopes one after another (the last one repeats)."""
    items = list(responses)

    def respond(_params):
        return items.pop(0) if len(items) > 1 else items[0]
    return respond


def make_runtime(settings, **responses):
    fake = FakeClient(responses)
    return Runtime(settings, fake, ApprovalStore()), fake


DELETED = user_row(4, "deleted", 1)
NORMAL = user_row(1, "normal", 0)


# ---- placement and lookups ----------------------------------------------------------------------

def test_new_tools_are_placed_as_decided():
    by_name = {ep.tool: ep for ep in ALL}
    for name in ("mb_list_ticket_categories", "mb_list_ticket_priorities", "mb_list_ticket_performers"):
        assert by_name[name].scope == "read" and not by_name[name].gated
    for name in ("mb_restore_user", "mb_delete_user_device"):
        assert by_name[name].scope == "account_state" and by_name[name].gated


@pytest.mark.parametrize("tool,uri", [
    ("mb_list_ticket_categories", "/json/ticketview/getcategorieslist"),
    ("mb_list_ticket_priorities", "/json/ticketview/getprioritiestypeslist"),
    ("mb_list_ticket_performers", "/extjs/ticketview/getPerformerList"),
])
async def test_lookup_tools_return_the_rows(settings, tool, uri):
    rt, fake = make_runtime(settings, **{uri: {"success": True, "totalCount": 2,
                                                "data": [{"id": "1", "login": "a"}, {"id": "2", "login": "b"}]}})
    r = await rt.execute(tool, {}, READ_WRITE)
    assert r["ok"] and len(r["data"]) == 2 and r["totalCount"] == 2
    assert fake.posted(uri) == [{}]
    bad = await rt.execute(tool, {"x": 1}, READ_WRITE)
    assert bad["error"]["code"] == "validation"


# ---- devices --------------------------------------------------------------------------------------

async def test_get_user_devices_sends_useruid(settings):
    rt, fake = make_runtime(settings, **{DEVICES: {"success": True, "data": [{"devid": "350"}]}})
    r = await rt.execute("mb_get_user_devices", {"uid": 888}, READ_WRITE)
    assert r["ok"] and r["data"] == [{"devid": "350"}]
    assert fake.posted(DEVICES) == [{"useruid": "888"}]  # uid would return {} on the real Mikbill


async def test_delete_dev_type_is_sent_as_delete_list(settings):
    rt, fake = make_runtime(settings)
    args = {"devtypeid": 30}
    token = (await rt.execute("mb_delete_dev_type", gated(args), ALL_SCOPE_SET))["data"]["approval_token"]
    r = await rt.execute("mb_delete_dev_type", gated(args, dry_run=False, approval_token=token), ALL_SCOPE_SET)
    assert r["ok"] and fake.posted("/json/users/deletedevtype") == [{"delete_list": "30"}]


async def test_delete_user_device_previews_the_device_and_sends_useruid_and_devid(settings):
    device = {"devid": "351", "devtypeid": "30", "devtypename": "Router", "enabled": "1", "cost": "0", "rent": "0",
              "sell_day": "2026-11-15", "extra_info": [], "display_data": "secret-looking"}
    rt, fake = make_runtime(settings, **{DEVICES: {"success": True, "data": [device]}})
    args = {"uid": 888, "devid": 351}
    dry = await rt.execute("mb_delete_user_device", gated(args), ALL_SCOPE_SET)
    assert dry["data"]["summary"]["targets"] == [{k: device[k] for k in
                                                  ("devid", "devtypeid", "devtypename", "enabled", "cost", "rent", "sell_day")}]
    assert dry["data"]["request"]["params"] == {"useruid": "888", "devid": "351"}
    assert fake.posted("/extjs/users/deleteuserdev") == []
    r = await rt.execute("mb_delete_user_device", gated(args, dry_run=False,
                                                        approval_token=dry["data"]["approval_token"]), ALL_SCOPE_SET)
    assert r["ok"] and fake.posted("/extjs/users/deleteuserdev") == [{"useruid": "888", "devid": "351"}]


async def test_delete_user_device_reports_an_unknown_device(settings):
    rt, _ = make_runtime(settings, **{DEVICES: {"success": True, "data": []}})
    dry = await rt.execute("mb_delete_user_device", gated({"uid": 888, "devid": 999}), ALL_SCOPE_SET)
    assert dry["data"]["summary"]["targets"] == [{"devid": 999, "listed": False}]


# ---- restore ---------------------------------------------------------------------------------------

async def test_restore_without_gid_uses_enableusersmulti(settings):
    rt, fake = make_runtime(settings, **{USER: sequence(DELETED, DELETED, NORMAL)})
    args = {"uid": 7}
    dry = await rt.execute("mb_restore_user", gated(args), ALL_SCOPE_SET)
    assert dry["ok"] and dry["dry_run"] is True
    assert dry["data"]["request"] == {"method": "POST", "uri": ENABLE, "params": {"set_users_list": "7"}}
    assert dry["data"]["summary"]["targets"][0]["status"] == "deleted"
    assert any("soft-deleted" in w for w in dry["data"]["warnings"])
    assert fake.posted(ENABLE) == [] and "plain" not in str(dry)
    r = await rt.execute("mb_restore_user", gated(args, dry_run=False, approval_token=dry["data"]["approval_token"]),
                         ALL_SCOPE_SET)
    assert r["ok"] and r["data"] == {"restored": True, "method": "enableusersmulti"}
    assert fake.posted(ENABLE) == [{"set_users_list": "7"}] and fake.posted(VKL) == []
    assert r["state_before"][0]["state"] == "4" and r["state_after"][0]["state"] == "1"
    assert "plain" not in str(r)


async def test_restore_with_gid_uses_vkluserflex_with_the_ui_parameters(settings):
    rt, fake = make_runtime(settings, **{USER: sequence(DELETED, DELETED, NORMAL)})
    args = {"uid": 7, "gid": 49, "itog_summa": 5}
    dry = await rt.execute("mb_restore_user", gated(args), ALL_SCOPE_SET)
    assert dry["data"]["request"]["uri"] == VKL
    r = await rt.execute("mb_restore_user", gated(args, dry_run=False, approval_token=dry["data"]["approval_token"]),
                         ALL_SCOPE_SET)
    assert r["ok"] and r["data"]["method"] == "vkluserflex"
    assert fake.posted(VKL) == [{"uid": "7", "gid": "49", "itog_summa": "5", "type_list": "2"}]
    assert fake.posted(ENABLE) == []


async def test_restore_gid_defaults_the_activation_price_to_zero(settings):
    rt, fake = make_runtime(settings, **{USER: sequence(DELETED, DELETED, NORMAL)})
    dry = await rt.execute("mb_restore_user", gated({"uid": 7, "gid": 1}), ALL_SCOPE_SET)
    assert dry["data"]["request"]["params"]["itog_summa"] == "0"


async def test_restore_refuses_a_user_that_is_not_deleted(settings):
    rt, fake = make_runtime(settings, **{USER: NORMAL})
    dry = await rt.execute("mb_restore_user", gated({"uid": 7}), ALL_SCOPE_SET)
    assert dry["ok"] is False and dry["error"]["code"] == "precondition_failed"
    assert fake.posted(ENABLE) == [] and fake.posted(VKL) == []


async def test_restore_fails_loudly_if_the_user_is_still_deleted(settings):
    rt, fake = make_runtime(settings, **{USER: DELETED})
    args = {"uid": 7}
    dry = await rt.execute("mb_restore_user", gated(args), ALL_SCOPE_SET)
    r = await rt.execute("mb_restore_user", gated(args, dry_run=False, approval_token=dry["data"]["approval_token"]),
                         ALL_SCOPE_SET)
    assert r["ok"] is False and r["error"]["code"] == "upstream_error" and "still deleted" in r["error"]["message"]


async def test_restore_itog_summa_needs_gid(settings):
    rt, _ = make_runtime(settings, **{USER: DELETED})
    r = await rt.execute("mb_restore_user", gated({"uid": 7, "itog_summa": 5}), ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation"


async def test_restore_token_is_bound_to_the_parameters(settings):
    rt, fake = make_runtime(settings, **{USER: DELETED})
    dry = await rt.execute("mb_restore_user", gated({"uid": 7}), ALL_SCOPE_SET)
    r = await rt.execute("mb_restore_user", gated({"uid": 7, "gid": 1}, dry_run=False,
                                                  approval_token=dry["data"]["approval_token"]), ALL_SCOPE_SET)
    assert r["error"]["code"] == "approval_required"
    assert fake.posted(ENABLE) == [] and fake.posted(VKL) == []


async def test_restore_is_gated_and_needs_the_account_state_scope(settings):
    rt, _ = make_runtime(settings, **{USER: DELETED})
    r = await rt.execute("mb_restore_user", gated({"uid": 7}), READ_WRITE)
    assert r["error"]["code"] == "scope_denied"
    r = await rt.execute("mb_restore_user", {"uid": 7}, ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation"  # no reason
