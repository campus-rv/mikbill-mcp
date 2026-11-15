import pytest

from conftest import FakeClient
from mb_mcp.endpoints import ALL
from mb_mcp.runtime import Runtime

READ = frozenset({"read"})
SEARCH = "/json/users/searchflex"
ONLINE = "/json/users/inetonlinefl"

# What the admin UI sends for an unfiltered search (captured from the demo): every state, internet "all", match all.
BASE = {"op": "1", "search_all_states": "1", "search_deleted_state": "0", "search_display_all": "1",
        "search_frozen_state": "0", "search_internet": "0", "search_normal_state": "0", "search_otkluchen_state": "0"}


def make_runtime(settings, **responses):
    fake = FakeClient(responses)
    return Runtime(settings, fake), fake


def rows(n, **extra):
    return [{"useruid": str(i), "user": f"u{i}", "gid": "38", "password": "plain", **extra} for i in range(1, n + 1)]


# ---- the tool definition ------------------------------------------------------------------------------------

def test_search_tool_lists_every_ui_filter_and_the_extras(settings):
    rt, _ = make_runtime(settings)
    ep = next(e for e in ALL if e.tool == "mb_search_users")
    schema = rt.describe(ep)["inputSchema"]
    props = schema["properties"]
    for name in ("uid", "user", "address", "phone", "local_ip", "sticky_note", "app", "settlementid", "neighborhoodid",
                 "laneid", "houseid", "gid", "swid", "sectorid", "switchport", "dogovor", "pon_central_closure_txt",
                 "pon_connection_box_txt", "states", "user_labels", "internet", "match", "limit"):
        assert name in props, name
        assert props[name].get("description") or name in ("dogovor",), name
    assert props["uid"]["type"] == "integer" and props["gid"]["type"] == "integer"
    assert props["states"]["items"]["enum"] == ["normal", "disabled", "frozen", "deleted"]
    assert props["limit"]["maximum"] == 1000 and props["limit"]["default"] == 100
    assert schema["required"] == [] and schema["additionalProperties"] is False


# ---- validation ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("args", [{}, {"match": "any"}, {"limit": 10}, {"user": ""}, {"user": "  "}])
async def test_a_search_without_a_filter_is_refused(settings, args):
    rt, fake = make_runtime(settings)
    r = await rt.execute("mb_search_users", args, READ)
    assert r["error"]["code"] == "validation" and "at least one" in r["error"]["message"]
    assert fake.calls == []


async def test_uid_cannot_be_combined_with_other_filters(settings):
    rt, fake = make_runtime(settings)
    r = await rt.execute("mb_search_users", {"uid": 122, "user": "x"}, READ)
    assert r["error"]["code"] == "validation" and "uid cannot be combined" in r["error"]["message"]
    r = await rt.execute("mb_search_users", {"uid": 122, "states": ["normal"]}, READ)
    assert r["error"]["code"] == "validation"
    assert fake.calls == []


@pytest.mark.parametrize("bad", ["02_122", "1,2", "12abc", 1.5, True, "", [1], None])
async def test_numeric_filters_must_be_whole_numbers(settings, bad):
    # Mikbill reads "02_122" as 2 and "1,2" as 1, so a sloppy value would search for another subscriber
    rt, fake = make_runtime(settings)
    r = await rt.execute("mb_search_users", {"uid": bad}, READ)
    assert r["error"]["code"] == "validation"
    assert fake.calls == []


@pytest.mark.parametrize("extra", [{"states": ["normal"]}, {"internet": "on"}])
async def test_match_any_is_refused_with_states_or_internet(settings, extra):
    # Mikbill ORs the status and internet selectors into the result when op is 0 (verified on the demo)
    rt, fake = make_runtime(settings)
    r = await rt.execute("mb_search_users", {"gid": 38, "match": "any", **extra}, READ)
    assert r["error"]["code"] == "validation" and "match any" in r["error"]["message"]
    assert fake.calls == []
    ok = await rt.execute("mb_search_users", {"gid": 38, "swid": 7, "match": "any", "user_labels": [1]}, READ)
    assert ok["ok"]  # any works across the text, id and label filters


async def test_digit_strings_are_accepted_for_numeric_filters(settings):
    rt, fake = make_runtime(settings, **{SEARCH: {"success": True, "data": []}})
    assert (await rt.execute("mb_search_users", {"gid": "38"}, READ))["ok"]
    assert fake.posted(SEARCH)[0]["gid"] == "38"


@pytest.mark.parametrize("args", [
    {"user": "x", "states": []}, {"user": "x", "states": ["gone"]}, {"user": "x", "states": ["normal", "normal"]},
    {"user": "x", "states": "normal"}, {"user": "x", "user_labels": []}, {"user": "x", "user_labels": ["a"]},
    {"user": "x", "internet": "maybe"}, {"user": "x", "match": "some"}, {"dogovor": 9}, {"user": "x", "limit": 0},
    {"user": "x", "limit": 1001}, {"user": "x", "bogus": 1},
])
async def test_bad_search_arguments_are_refused(settings, args):
    rt, fake = make_runtime(settings)
    r = await rt.execute("mb_search_users", args, READ)
    assert r["error"]["code"] == "validation"
    assert fake.calls == []


# ---- the request ----------------------------------------------------------------------------------------------

async def test_a_text_filter_sends_the_ui_form_with_all_states(settings):
    rt, fake = make_runtime(settings, **{SEARCH: {"success": True, "totalCount": 1, "data": rows(1)}})
    r = await rt.execute("mb_search_users", {"address": "Воздвиженська"}, READ)
    assert r["ok"] and fake.posted(SEARCH) == [{**BASE, "address": "Воздвиженська"}]


async def test_every_filter_is_sent_under_the_names_the_ui_uses(settings):
    rt, fake = make_runtime(settings)
    args = {"user": "kra", "address": "a", "phone": "38", "local_ip": "10.10", "sticky_note": "n", "app": "66",
            "settlementid": 5, "neighborhoodid": 7, "laneid": 16, "houseid": 46, "gid": 38, "swid": 7, "sectorid": 13,
            "switchport": "1", "dogovor": 1, "pon_central_closure_txt": "c", "pon_connection_box_txt": "b"}
    await rt.execute("mb_search_users", args, READ)
    assert fake.posted(SEARCH) == [{**BASE, **{k: str(v) for k, v in args.items()}}]


async def test_states_internet_labels_and_match_become_the_ui_flags(settings):
    rt, fake = make_runtime(settings)
    await rt.execute("mb_search_users", {"gid": 38, "states": ["normal", "frozen"], "internet": "on",
                                         "user_labels": [1, 2]}, READ)
    assert fake.posted(SEARCH) == [{**BASE, "gid": "38", "search_all_states": "0", "search_normal_state": "1",
                                    "search_frozen_state": "1", "search_internet": "1", "user_labels": "[1, 2]"}]
    fake.calls.clear()
    await rt.execute("mb_search_users", {"gid": 38, "swid": 7, "match": "any"}, READ)
    assert fake.posted(SEARCH)[0]["op"] == "0"


async def test_each_state_and_internet_value_maps_to_its_flag(settings):
    rt, fake = make_runtime(settings)
    for state, flag in (("normal", "search_normal_state"), ("disabled", "search_otkluchen_state"),
                        ("frozen", "search_frozen_state"), ("deleted", "search_deleted_state")):
        fake.calls.clear()
        await rt.execute("mb_search_users", {"user": "x", "states": [state]}, READ)
        sent = fake.posted(SEARCH)[0]
        assert sent[flag] == "1" and sent["search_all_states"] == "0"
        assert [k for k in ("search_normal_state", "search_otkluchen_state", "search_frozen_state",
                            "search_deleted_state") if sent[k] == "1"] == [flag]
    await rt.execute("mb_search_users", {"user": "x", "internet": "off"}, READ)
    assert fake.posted(SEARCH)[-1]["search_internet"] == "2"


async def test_the_limit_is_not_sent_to_mikbill(settings):
    rt, fake = make_runtime(settings)
    await rt.execute("mb_search_users", {"user": "x", "limit": 5}, READ)
    assert "limit" not in fake.posted(SEARCH)[0]


# ---- the result -----------------------------------------------------------------------------------------------

async def test_rows_are_capped_at_limit_and_the_result_says_so(settings):
    rt, _ = make_runtime(settings, **{SEARCH: {"success": True, "totalCount": 5, "data": rows(5)}})
    r = await rt.execute("mb_search_users", {"gid": 38, "limit": 2}, READ)
    assert [x["useruid"] for x in r["data"]] == ["1", "2"]
    assert r["totalCount"] == 5 and r["truncated"] is True


async def test_the_default_limit_is_100(settings):
    rt, _ = make_runtime(settings, **{SEARCH: {"success": True, "totalCount": 150, "data": rows(150)}})
    r = await rt.execute("mb_search_users", {"gid": 38}, READ)
    assert len(r["data"]) == 100 and r["totalCount"] == 150 and r["truncated"] is True


async def test_a_small_result_is_not_truncated_and_passwords_are_masked(settings):
    rt, _ = make_runtime(settings, **{SEARCH: {"success": True, "totalCount": 3, "data": rows(3)}})
    r = await rt.execute("mb_search_users", {"gid": 38}, READ)
    assert len(r["data"]) == 3 and r["truncated"] is False
    assert "plain" not in str(r)


# ---- mb_list_online_users -------------------------------------------------------------------------------------

def online_rows():
    return [
        {"useruid": "1", "user": "a", "gid": "38", "sectorid": "1", "swid": "10", "settlementid": "1", "ip": "10.0.0.1"},
        {"useruid": "2", "user": "b", "gid": "5", "sectorid": "1", "swid": "4", "settlementid": "1", "ip": "10.0.0.2"},
        {"useruid": "3", "user": "c", "gid": "38", "sectorid": "13", "swid": "10", "settlementid": "5", "ip": "10.0.0.3"},
    ]


def test_online_tool_is_a_read_tool():
    ep = next(e for e in ALL if e.tool == "mb_list_online_users")
    assert ep.scope == "read" and not ep.gated and ep.uri == ONLINE


async def test_online_users_are_listed_without_parameters(settings):
    rt, fake = make_runtime(settings, **{ONLINE: {"success": True, "totalCount": 3, "data": online_rows()}})
    r = await rt.execute("mb_list_online_users", {}, READ)
    assert r["ok"] and len(r["data"]) == 3 and r["totalCount"] == 3 and r["truncated"] is False
    assert fake.posted(ONLINE) == [{}]


async def test_online_users_are_filtered_on_the_rows_and_nothing_extra_is_sent(settings):
    # Mikbill ignores filter parameters for this endpoint (verified), so they must not be sent as if they worked
    rt, fake = make_runtime(settings, **{ONLINE: {"success": True, "totalCount": 3, "data": online_rows()}})
    r = await rt.execute("mb_list_online_users", {"gid": 38}, READ)
    assert [x["user"] for x in r["data"]] == ["a", "c"] and r["totalCount"] == 2
    r = await rt.execute("mb_list_online_users", {"gid": "38", "sectorid": 13, "swid": 10}, READ)
    assert [x["user"] for x in r["data"]] == ["c"]
    r = await rt.execute("mb_list_online_users", {"useruid": 2}, READ)
    assert [x["user"] for x in r["data"]] == ["b"]
    r = await rt.execute("mb_list_online_users", {"user": "a"}, READ)
    assert [x["useruid"] for x in r["data"]] == ["1"]
    assert fake.posted(ONLINE) == [{}, {}, {}, {}]


async def test_online_users_limit_and_validation(settings):
    rt, _ = make_runtime(settings, **{ONLINE: {"success": True, "totalCount": 3, "data": online_rows()}})
    r = await rt.execute("mb_list_online_users", {"limit": 2}, READ)
    assert len(r["data"]) == 2 and r["totalCount"] == 3 and r["truncated"] is True
    for bad in ({"gid": "x"}, {"limit": 0}, {"bogus": 1}, {"useruid": 1.5}):
        assert (await rt.execute("mb_list_online_users", bad, READ))["error"]["code"] == "validation", bad


async def test_online_users_need_the_read_scope(settings):
    rt, _ = make_runtime(settings)
    r = await rt.execute("mb_list_online_users", {}, frozenset({"write"}))
    assert r["error"]["code"] == "scope_denied"
