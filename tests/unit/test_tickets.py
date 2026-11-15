import json

import httpx
import pytest
import respx

from conftest import FakeClient
from mb_mcp.client import MikbillClient
from mb_mcp.config import Settings
from mb_mcp.endpoints import ALL
from mb_mcp.errors import MikbillError

READ_WRITE = frozenset({"read", "write"})
LIST = "/extjs/ticketview/getticketslist2x"
PUTIN = "/extjs/ticketedit/putticketin"
STUFF = "/json/index/getstuffproper"

ROWS = [
    {"ticketid": "59", "statustypeid": "1", "categoryid": "2", "prioritytypeid": "1", "rating": "0",
     "creationdate": "2026-11-15 17:08:02", "fio": "Real Name", "phones": "555", "notes": [{"noteid": "1"}]},
    {"ticketid": "60", "statustypeid": "3", "categoryid": "1", "prioritytypeid": "2", "rating": "0",
     "creationdate": "2026-11-15 17:09:00", "fio": "Other", "phones": "", "notes": []},
]
COUNTERS = {"status": {"1": 1, "2": 0, "3": 1, "4": 0}}


@pytest.fixture
def tfake():
    return FakeClient({
        LIST: {"success": True, "data": [dict(r) for r in ROWS], "counters": COUNTERS},
        STUFF: {"success": True, "data": {"stuff_propers": {"stuffid": 2, "login": "demo"}}},
    })


@pytest.fixture
def trt(settings, tfake):
    from mb_mcp.runtime import Runtime
    return Runtime(settings, tfake)


def gated(args, **extra):
    return {**args, "reason": "unit test", **extra}


# ---- placement ---------------------------------------------------------------------------------

def test_ticket_tools_scopes():
    by_name = {ep.tool: ep for ep in ALL}
    for name in ("mb_list_tickets", "mb_get_ticket_notes", "mb_get_ticket_logs"):
        assert by_name[name].scope == "read" and not by_name[name].gated
    for name in ("mb_create_ticket", "mb_update_ticket", "mb_add_ticket_note", "mb_set_ticket_status",
                 "mb_delete_ticket_note"):
        assert by_name[name].scope == "write" and not by_name[name].gated
    # cannot be undone: in the write scope, but with a reason and a preview by default
    assert by_name["mb_delete_tickets"].scope == "write" and by_name["mb_delete_tickets"].gated


def test_status_tool_schema_has_enums(trt):
    ep = next(e for e in ALL if e.tool == "mb_set_ticket_status")
    props = trt.describe(ep)["inputSchema"]["properties"]
    assert props["status"]["enum"] == ["open", "in_work", "performed", "closed"]
    assert props["rating"]["enum"] == [1, 2, 3, 4, 5]
    assert props["ticket_ids"]["type"] == "array"


# ---- reads --------------------------------------------------------------------------------------

async def test_list_tickets_returns_rows_and_counters(trt, tfake):
    r = await trt.execute("mb_list_tickets", {}, READ_WRITE)
    assert r["ok"] and [t["ticketid"] for t in r["data"]] == ["59", "60"]
    assert r["counters"] == COUNTERS
    assert tfake.posted(LIST) == [{}]


async def test_list_filters_are_applied_to_rows_and_not_sent(trt, tfake):
    r = await trt.execute("mb_list_tickets", {"statustypeid": 3}, READ_WRITE)
    assert [t["ticketid"] for t in r["data"]] == ["60"]
    assert tfake.posted(LIST) == [{}]  # Mikbill ignores filter parameters, so none are sent
    r = await trt.execute("mb_list_tickets", {"categoryid": 2, "prioritytypeid": 1}, READ_WRITE)
    assert [t["ticketid"] for t in r["data"]] == ["59"]
    r = await trt.execute("mb_list_tickets", {"ticketid": 999}, READ_WRITE)
    assert r["data"] == []


async def test_notes_and_logs_send_ticketid(trt, tfake):
    await trt.execute("mb_get_ticket_notes", {"ticketid": 59}, READ_WRITE)
    await trt.execute("mb_get_ticket_logs", {"ticketid": 59}, READ_WRITE)
    assert tfake.posted("/json/ticketview/getnotes") == [{"ticketid": "59"}]
    assert tfake.posted("/json/ticketview/getlogs") == [{"ticketid": "59"}]
    r = await trt.execute("mb_get_ticket_notes", {}, READ_WRITE)
    assert r["error"]["code"] == "validation"


# ---- client: the list endpoint has no `success` field -------------------------------------------

BASE = "https://mikbill.example.test"


@respx.mock
async def test_envelope_without_success_is_accepted_only_when_asked():
    settings = Settings(base_url=BASE, login="a", password="b", scopes=frozenset({"read"}))
    client = MikbillClient(settings)
    try:
        respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json={"success": True}))
        respx.post(f"{BASE}{LIST}").mock(return_value=httpx.Response(200, json={"data": [], "counters": {}}))
        env = await client.call(LIST, {}, no_success_key=True)
        assert env["success"] is True and env["data"] == [] and env["counters"] == {}
        with pytest.raises(MikbillError) as e:
            await client.call(LIST, {})
        assert e.value.code == "upstream_error"
    finally:
        await client.aclose()


# ---- writes ---------------------------------------------------------------------------------------

async def test_create_ticket_sends_defaults_and_fields(trt, tfake):
    tfake.responses["/extjs/ticketedit/setticket2x"] = {"success": True, "data": [{"ticketid": "70"}]}
    r = await trt.execute("mb_create_ticket", {"categoryid": 1, "prioritytypeid": 2, "useruid": 860, "emphasis": 1,
                                               "order_number": "X-1"}, READ_WRITE)
    assert r["ok"] and r["data"] == [{"ticketid": "70"}]
    assert tfake.posted("/extjs/ticketedit/setticket2x") == [
        {"fio": "", "phones": "", "app": "", "categoryid": "1", "prioritytypeid": "2", "useruid": "860",
         "emphasis": "1", "order_number": "X-1"}]


async def test_create_ticket_requires_category_and_priority(trt):
    r = await trt.execute("mb_create_ticket", {"categoryid": 1}, READ_WRITE)
    assert r["error"]["code"] == "validation" and "prioritytypeid" in r["error"]["message"]


async def test_update_ticket_sends_only_given_fields(trt, tfake):
    await trt.execute("mb_update_ticket", {"ticketid": 59, "emphasis": 0}, READ_WRITE)
    assert tfake.posted("/extjs/ticketedit/setticket2x") == [{"ticketid": "59", "emphasis": "0"}]


async def test_add_note(trt, tfake):
    await trt.execute("mb_add_ticket_note", {"ticketid": 59, "note": "called the customer"}, READ_WRITE)
    assert tfake.posted("/extjs/ticketedit/addnote") == [{"ticketid": "59", "note": "called the customer"}]


# ---- status changes -------------------------------------------------------------------------------

Z = "0000-00-00 00:00:00"


@pytest.mark.parametrize("status,extra", [
    ("in_work", {"statustypeid": "3", "closingdate": Z, "performed_by_stuffid": "0", "closed_by_stuffid": "0",
                 "performed_without_on_site_visit": "0", "inwork_by_stuffid": "2", "can_not_be_performed": "0",
                 "performingdate": Z}),
    ("performed", {"statustypeid": "4", "closingdate": Z, "performed_by_stuffid": "2", "closed_by_stuffid": "0",
                   "performed_without_on_site_visit": "0", "inwork_by_stuffid": "0", "can_not_be_performed": "0"}),
    ("open", {"statustypeid": "1", "closingdate": Z, "performed_by_stuffid": "2", "inworkdate": Z,
              "closed_by_stuffid": "0", "performed_without_on_site_visit": "0", "inwork_by_stuffid": "0",
              "can_not_be_performed": "0", "performingdate": Z}),
])
async def test_status_requests_match_what_the_admin_ui_sends(trt, tfake, status, extra):
    r = await trt.execute("mb_set_ticket_status", {"status": status, "ticket_ids": [59, 60]}, READ_WRITE)
    assert r["ok"]
    assert tfake.posted(PUTIN) == [{"set_list": "59,60", "rating": "0", **extra}]


async def test_closing_sends_rating_and_the_closer(trt, tfake):
    await trt.execute("mb_set_ticket_status", {"status": "closed", "ticket_ids": [59], "rating": 5}, READ_WRITE)
    assert tfake.posted(PUTIN) == [{"set_list": "59", "statustypeid": "2", "rating": "5", "closed_by_stuffid": "2"}]


async def test_acting_staff_id_is_fetched_once(trt, tfake):
    for status in ("in_work", "performed"):
        await trt.execute("mb_set_ticket_status", {"status": status, "ticket_ids": [59]}, READ_WRITE)
    assert len(tfake.posted(STUFF)) == 1


async def test_status_result_reads_back_the_tickets(trt, tfake):
    r = await trt.execute("mb_set_ticket_status", {"status": "in_work", "ticket_ids": [59, 999]}, READ_WRITE)
    after = {t["ticketid"]: t for t in r["state_after"]}
    assert after[59]["statustypeid"] == "1"  # the fake list does not change; the read-back is what the list reports
    assert after[999] == {"ticketid": 999, "listed": False}


async def test_status_without_staff_id_is_an_error(trt, tfake):
    tfake.responses[STUFF] = {"success": True, "data": {}}
    r = await trt.execute("mb_set_ticket_status", {"status": "in_work", "ticket_ids": [59]}, READ_WRITE)
    assert r["error"]["code"] == "upstream_error"
    assert tfake.posted(PUTIN) == []


@pytest.mark.parametrize("args", [
    {"status": "done", "ticket_ids": [1]},                    # unknown status
    {"status": "closed", "ticket_ids": [1], "rating": 6},     # rating out of range
    {"status": "closed", "ticket_ids": [1], "rating": True},  # bool is not a rating
    {"status": "in_work", "ticket_ids": [1], "rating": 5},    # rating only with closed
    {"status": "closed"},                                     # no tickets
    {"status": "closed", "ticket_ids": []},
    {"status": "closed", "ticket_ids": [1, 1]},               # duplicates
    {"status": "closed", "ticket_ids": [1, "2"]},
    {"status": "closed", "ticket_ids": list(range(1, 7))},    # over MB_MAX_BULK (5 in the test settings)
])
async def test_status_validation(trt, tfake, args):
    r = await trt.execute("mb_set_ticket_status", args, READ_WRITE)
    assert r["ok"] is False and r["error"]["code"] == "validation", r
    assert tfake.posted(PUTIN) == []


# ---- deletes (write scope: the client asks the user, the server does not preview) ------------------------------

async def test_delete_tickets_previews_by_default_and_shows_the_tickets(trt, tfake):
    r = await trt.execute("mb_delete_tickets", {"ticket_ids": [59, 60], "reason": "duplicates"}, READ_WRITE)
    assert r["ok"] and r["dry_run"] is True
    assert [t["ticketid"] for t in r["data"]["summary"]["targets"]] == [59, 60] and r["data"]["summary"]["count"] == 2
    assert "Real Name" not in json.dumps(r)
    assert tfake.posted("/extjs/ticketedit/deletetickets") == []


async def test_delete_tickets_sends_enumerated_ids_and_reads_the_state_back(trt, tfake):
    r = await trt.execute("mb_delete_tickets", {"ticket_ids": [59, 60], "reason": "duplicates", "dry_run": False},
                          READ_WRITE)
    assert r["ok"] and "dry_run" not in r
    assert tfake.posted("/extjs/ticketedit/deletetickets") == [{"ticketid0": "59", "ticketid1": "60"}]
    assert [t["ticketid"] for t in r["state_after"]] == [59, 60]
    assert "Real Name" not in json.dumps(r)  # no personal data in the read-back


async def test_delete_tickets_needs_a_reason_and_deleting_a_note_takes_none(trt, tfake):
    r = await trt.execute("mb_delete_tickets", {"ticket_ids": [59], "dry_run": False}, READ_WRITE)
    assert r["error"]["code"] == "validation" and "reason" in r["error"]["message"]
    for extra in ({"reason": "x y z"}, {"dry_run": True}):
        r = await trt.execute("mb_delete_ticket_note", {"noteid": 33, **extra}, READ_WRITE)
        assert r["error"]["code"] == "validation", extra
    assert tfake.calls == []


async def test_delete_tickets_needs_the_write_scope(trt, tfake):
    r = await trt.execute("mb_delete_tickets", {"ticket_ids": [59]}, frozenset({"read"}))
    assert r["error"]["code"] == "scope_denied"
    assert tfake.posted("/extjs/ticketedit/deletetickets") == []


async def test_delete_note_sends_noteid(trt, tfake):
    r = await trt.execute("mb_delete_ticket_note", {"noteid": 33}, READ_WRITE)
    assert r["ok"] and tfake.posted("/extjs/ticketedit/deletenotes") == [{"noteid": "33"}]


async def test_ticket_notes_are_not_written_to_the_audit_log(settings, tfake, tmp_path):
    from dataclasses import replace

    from mb_mcp.audit import AuditLog
    from mb_mcp.runtime import Runtime
    path = tmp_path / "audit.log"
    rt = Runtime(replace(settings), tfake, AuditLog(str(path)))
    await rt.execute("mb_add_ticket_note", {"ticketid": 59, "note": "customer said secret-phrase"}, READ_WRITE)
    text = path.read_text(encoding="utf-8")
    assert "secret-phrase" not in text and "mb_add_ticket_note" in text
