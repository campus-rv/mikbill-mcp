"""limit and offset on the list tools (L12), and the packet list losing its dead paging fields (L1).

Mikbill does not page these lists (the packet list answered all 56 tariffs for page, start and limit alike), so the
server slices the rows itself: `limit` caps a response, `offset` reads the next page.
"""

import pytest

from conftest import FakeClient
from mb_mcp.endpoints import ALL
from mb_mcp.runtime import Runtime
from mb_mcp.schemas import DEFAULT_LIST_ROWS, MAX_ROWS, SCHEMAS

READ = frozenset({"read"})
HOUSES = "/json/users/housesfulllist"
PACKETS = "/json/tarif/gettarifsfl"
ONLINE = "/json/users/inetonlinefl"
SEARCH = "/json/users/searchflex"

LISTS = ("mb_list_dev_types", "mb_list_settlements", "mb_list_neighborhoods", "mb_list_lanes", "mb_list_houses",
         "mb_list_sectors", "mb_list_switches", "mb_list_switch_types", "mb_list_packets", "mb_list_user_groups",
         "mb_get_user_payment_history", "mb_get_user_short_history")


def rows(n):
    return [{"houseid": str(i), "house": f"h{i}"} for i in range(1, n + 1)]


def make_runtime(settings, **responses):
    fake = FakeClient(responses)
    return Runtime(settings, fake), fake


async def listing(rt, args=None):
    return await rt.execute("mb_list_houses", args or {}, READ)


# ---- the tool definitions -------------------------------------------------------------------------------------

@pytest.mark.parametrize("tool", LISTS)
def test_the_list_tools_take_limit_and_offset(settings, tool):
    assert SCHEMAS[tool].row_limit == DEFAULT_LIST_ROWS == 500
    ep = next(e for e in ALL if e.tool == tool)
    props = Runtime(settings, FakeClient()).describe(ep)["inputSchema"]["properties"]
    assert props["limit"]["default"] == 500 and props["limit"]["maximum"] == MAX_ROWS == 1000
    assert props["offset"]["minimum"] == 0 and props["offset"]["default"] == 0
    assert "next_offset" in props["offset"]["description"]


@pytest.mark.parametrize("tool", ["mb_search_users", "mb_list_online_users", "mb_list_tickets"])
def test_search_online_and_tickets_gain_an_offset_and_keep_their_default_limits(settings, tool):
    ep = next(e for e in ALL if e.tool == tool)
    props = Runtime(settings, FakeClient()).describe(ep)["inputSchema"]["properties"]
    assert "offset" in props and props["limit"]["default"] == SCHEMAS[tool].row_limit != 500


def test_tools_without_a_row_limit_have_neither():
    for ep in ALL:
        if SCHEMAS[ep.tool].row_limit is None:
            assert "limit" not in SCHEMAS[ep.tool].fields and "offset" not in SCHEMAS[ep.tool].fields, ep.tool


# ---- slicing ---------------------------------------------------------------------------------------------------

async def test_a_long_list_is_capped_at_500_and_says_how_to_continue(settings):
    rt, fake = make_runtime(settings, **{HOUSES: {"success": True, "data": rows(1200)}})
    r = await listing(rt)
    assert len(r["data"]) == 500 and r["data"][0]["houseid"] == "1" and r["data"][-1]["houseid"] == "500"
    assert r["totalCount"] == 1200 and r["truncated"] is True and r["next_offset"] == 500 and "offset" not in r


async def test_offset_reads_the_next_pages_until_the_end(settings):
    rt, fake = make_runtime(settings, **{HOUSES: {"success": True, "data": rows(1200)}})
    seen = []
    offset = 0
    while True:
        r = await listing(rt, {"offset": offset} if offset else {})
        seen += [x["houseid"] for x in r["data"]]
        if not r["truncated"]:
            assert "next_offset" not in r
            break
        assert r["next_offset"] == offset + 500
        offset = r["next_offset"]
    assert seen == [str(i) for i in range(1, 1201)]  # every row once, in order
    assert r["offset"] == 1000 and len(r["data"]) == 200 and r["totalCount"] == 1200


async def test_a_page_in_the_middle_with_its_own_limit(settings):
    rt, _ = make_runtime(settings, **{HOUSES: {"success": True, "data": rows(50)}})
    r = await listing(rt, {"limit": 10, "offset": 20})
    assert [x["houseid"] for x in r["data"]] == [str(i) for i in range(21, 31)]
    assert r["truncated"] is True and r["next_offset"] == 30 and r["offset"] == 20


async def test_the_largest_page_is_1000_rows(settings):
    rt, _ = make_runtime(settings, **{HOUSES: {"success": True, "data": rows(1500)}})
    r = await listing(rt, {"limit": 1000})
    assert len(r["data"]) == 1000 and r["next_offset"] == 1000


async def test_an_offset_past_the_end_is_an_empty_page_not_an_error(settings):
    rt, _ = make_runtime(settings, **{HOUSES: {"success": True, "data": rows(5)}})
    r = await listing(rt, {"offset": 50})
    assert r["ok"] and r["data"] == [] and r["truncated"] is False and r["totalCount"] == 5


async def test_a_short_list_is_not_truncated(settings):
    rt, _ = make_runtime(settings, **{HOUSES: {"success": True, "data": rows(92)}})
    r = await listing(rt)
    assert len(r["data"]) == 92 and r["truncated"] is False and "next_offset" not in r and r["totalCount"] == 92


async def test_limit_and_offset_are_never_sent_to_mikbill(settings):
    rt, fake = make_runtime(settings, **{HOUSES: {"success": True, "data": rows(5)}})
    await listing(rt, {"limit": 2, "offset": 1})
    assert fake.posted(HOUSES) == [{}]


@pytest.mark.parametrize("args", [{"limit": 0}, {"limit": 1001}, {"limit": -1}, {"limit": "x"}, {"limit": 1.5},
                                  {"offset": -1}, {"offset": "abc"}, {"offset": 1.5}, {"offset": True}, {"offset": None}])
async def test_bad_limit_or_offset_is_refused_before_anything_is_sent(settings, args):
    rt, fake = make_runtime(settings, **{HOUSES: {"success": True, "data": rows(5)}})
    r = await listing(rt, args)
    assert r["error"]["code"] == "validation"
    assert fake.calls == []


async def test_a_list_tool_without_a_row_limit_still_refuses_offset(settings):
    rt, fake = make_runtime(settings)
    r = await rt.execute("mb_list_ticket_priorities", {"offset": 1}, READ)
    assert r["error"]["code"] == "validation" and "offset" in r["error"]["message"]
    assert fake.calls == []


# ---- filtered and searched lists page after the filter ---------------------------------------------------------

async def test_online_users_are_paged_after_the_filter(settings):
    online = [{"useruid": str(i), "user": f"u{i}", "gid": "38" if i % 2 else "5"} for i in range(1, 41)]
    rt, fake = make_runtime(settings, **{ONLINE: {"success": True, "totalCount": 40, "data": online}})
    r = await rt.execute("mb_list_online_users", {"gid": 38, "limit": 5, "offset": 5, "detail": "full"}, READ)
    assert [x["useruid"] for x in r["data"]] == ["11", "13", "15", "17", "19"]  # the 6th to the 10th of 20 matches
    assert r["totalCount"] == 20 and r["truncated"] is True and r["next_offset"] == 10
    assert fake.posted(ONLINE) == [{}]


async def test_search_offset_is_not_a_filter_and_not_sent(settings):
    rt, fake = make_runtime(settings, **{SEARCH: {"success": True, "totalCount": 300,
                                                  "data": [{"useruid": str(i), "user": f"u{i}"} for i in range(300)]}})
    alone = await rt.execute("mb_search_users", {"offset": 10}, READ)
    assert alone["error"]["code"] == "validation" and "at least one" in alone["error"]["message"]
    r = await rt.execute("mb_search_users", {"gid": 38, "limit": 100, "offset": 100}, READ)
    assert len(r["data"]) == 100 and r["data"][0]["useruid"] == "100" and r["next_offset"] == 200
    sent = fake.posted(SEARCH)[0]
    assert "offset" not in sent and "limit" not in sent and sent["gid"] == "38"


# ---- L1: mb_list_packets -----------------------------------------------------------------------------------------

async def test_the_packet_list_has_no_dead_paging_fields(settings):
    rt, fake = make_runtime(settings, **{PACKETS: {"success": True, "data": [{"gid": str(i)} for i in range(56)]}})
    for dead in ("page", "start"):
        r = await rt.execute("mb_list_packets", {dead: 1}, READ)
        assert r["error"]["code"] == "validation" and dead in r["error"]["message"]
    assert fake.calls == []
    r = await rt.execute("mb_list_packets", {"limit": 20, "offset": 10}, READ)
    assert [x["gid"] for x in r["data"]] == [str(i) for i in range(10, 30)]
    assert r["totalCount"] == 56 and r["truncated"] is True
    assert fake.posted(PACKETS) == [{}]  # nothing for Mikbill to misread: page/start/limit were ignored by it anyway
