"""mb_get_payment_info, mb_receive_payment and mb_set_credit: routes the admin UI uses that were missing.

Evidence for every behaviour below is in fixtures/payment_credit_info_verified.json (captured on the demo).
"""

import pytest

from conftest import ALL_SCOPE_SET, FakeClient
from mb_mcp.endpoints import ALL, tool_annotations
from mb_mcp.errors import error_from_envelope
from mb_mcp.runtime import Runtime

READ = frozenset({"read"})
INFO = "/json/users/howmatchpayflex"
PAYMENT = "/json/users/paymentflex"
CREDIT = "/extjs/users/credit"
USER = "/json/users/getuserdatambp"

RECEIPT = {"success": True, "error": 0, "data": [{
    "address": "Warszawa, Marcina Kasprzaka 1/1", "bughid": "1962", "bughtime": "16.11.2026 01:08:53",
    "company_id": "Demo", "credit": 0, "deposit": -1, "fio": "MCPTEST payflex", "ok": 0}]}


def make_runtime(settings, **responses):
    fake = FakeClient(responses)
    return Runtime(settings, fake), fake


def gated(args, **extra):
    return {**args, "reason": "unit test", **extra}


by_name = {ep.tool: ep for ep in ALL}


# ---- placement -------------------------------------------------------------------------------------------------

def test_the_tools_are_placed_as_decided():
    info = by_name["mb_get_payment_info"]
    assert info.scope == "read" and not info.gated and info.uri == INFO
    for name, uri in (("mb_receive_payment", PAYMENT), ("mb_set_credit", CREDIT)):
        assert by_name[name].scope == "finance" and by_name[name].gated and by_name[name].uri == uri, name
    # the routes that were already there are the ones the UI shares with these
    assert by_name["mb_create_payment"].uri == "/json/users/paymentdoflex"
    assert by_name["mb_remove_credit_single"].uri == "/json/users/creditnullfl"


def test_annotations_say_what_the_tools_do():
    assert tool_annotations(by_name["mb_get_payment_info"])["read_only_hint"] is True
    pay = tool_annotations(by_name["mb_receive_payment"])
    assert pay["destructive_hint"] is True and pay["idempotent_hint"] is False  # a second call is a second payment
    credit = tool_annotations(by_name["mb_set_credit"])
    assert credit["destructive_hint"] is True and credit["idempotent_hint"] is True  # setting the same value again


def test_descriptions_tell_the_payment_tools_apart(settings):
    rt, _ = make_runtime(settings)
    receive = rt.describe(by_name["mb_receive_payment"])["description"]
    operation = rt.describe(by_name["mb_create_payment"])["description"]
    assert "mb_create_payment" in receive and "mb_receive_payment" in operation
    assert "replaces" in rt.describe(by_name["mb_set_credit"])["description"]


# ---- mb_get_payment_info ---------------------------------------------------------------------------------------

async def test_payment_info_sends_the_uid_and_returns_the_row(settings):
    row = {"abonplata": "0", "abonplata_installments": "0", "abonplata_real_now": "75", "abonplata_subscriptions": "0",
           "abonplata_user_dev": "0", "date_itog": "05-06-2027 23:59", "days_left": 201, "deposit": 499.11,
           "itog_summa": "0.00", "total_payments": 75}
    rt, fake = make_runtime(settings, **{INFO: {"success": True, "error": 0, "data": [row]}})
    r = await rt.execute("mb_get_payment_info", {"uid": 1}, READ)
    assert r["ok"] and r["data"] == [row]
    assert fake.posted(INFO) == [{"uid": "1"}]


@pytest.mark.parametrize("args", [{}, {"uid": 0}, {"uid": "1,2"}, {"uid": 1, "bogus": 1}])
async def test_payment_info_needs_a_valid_uid(settings, args):
    rt, fake = make_runtime(settings)
    r = await rt.execute("mb_get_payment_info", args, READ)
    assert r["error"]["code"] == "validation"
    assert fake.calls == []


async def test_payment_info_without_a_uid_is_mikbills_error_25(settings):
    from mb_mcp.errors import MikbillError
    rt, _ = make_runtime(settings, **{INFO: MikbillError("upstream_error", "upstream error 25")})
    r = await rt.execute("mb_get_payment_info", {"uid": 5}, READ)
    assert r["ok"] is False and r["error"]["code"] == "upstream_error"


# ---- mb_receive_payment ----------------------------------------------------------------------------------------

async def test_a_payment_sends_what_the_ui_sends(settings):
    rt, fake = make_runtime(settings, **{PAYMENT: RECEIPT})
    r = await rt.execute("mb_receive_payment", gated({"uid": 915, "deposit": 5, "prim2": "cash at the desk"}),
                         ALL_SCOPE_SET)
    assert r["ok"] and "dry_run" not in r  # a single tool: it executes on the first call
    assert fake.posted(PAYMENT) == [{"uid": "915", "deposit": "5", "prim2": "cash at the desk"}]
    assert r["data"][0]["bughid"] == "1962"  # the receipt comes back


async def test_the_optional_ui_fields_are_sent_when_given(settings):
    rt, fake = make_runtime(settings, **{PAYMENT: RECEIPT})
    args = {"uid": 915, "deposit": 2.5, "prim2": "bank", "beznal_payment": 1, "beznal_date": "2026-11-15",
            "do_fiscalization": 0}
    await rt.execute("mb_receive_payment", gated(args), ALL_SCOPE_SET)
    assert fake.posted(PAYMENT) == [{"uid": "915", "deposit": "2.5", "prim2": "bank", "beznal_payment": "1",
                                     "beznal_date": "2026-11-15", "do_fiscalization": "0"}]


@pytest.mark.parametrize("deposit", [0, -3, "abc", "1e3", float("nan"), True, None])
async def test_zero_and_negative_amounts_are_refused_because_mikbill_ignores_them_silently(settings, deposit):
    # verified on the demo: deposit 0 or -3 answers success with nothing done, so it must not look like a payment
    rt, fake = make_runtime(settings, **{PAYMENT: RECEIPT})
    r = await rt.execute("mb_receive_payment", gated({"uid": 915, "deposit": deposit, "prim2": "x"}), ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation"
    assert fake.posted(PAYMENT) == []


async def test_a_payment_is_capped_by_mb_max_payment(settings):
    rt, fake = make_runtime(settings, **{PAYMENT: RECEIPT})
    r = await rt.execute("mb_receive_payment", gated({"uid": 1, "deposit": settings.max_payment * 2, "prim2": "x"}),
                         ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation" and "MB_MAX_PAYMENT" in r["error"]["message"]
    assert fake.posted(PAYMENT) == []


@pytest.mark.parametrize("extra", [{"beznal_payment": 2}, {"beznal_payment": "yes"}, {"do_fiscalization": 5},
                                   {"beznal_date": "15.11.2026"}, {"beznal_date": "2026-13-40"},
                                   {"beznal_date": "2026-11-15 10:00"}, {"beznal_date": ""}])
async def test_the_switches_and_the_date_are_checked(settings, extra):
    rt, fake = make_runtime(settings, **{PAYMENT: RECEIPT})
    r = await rt.execute("mb_receive_payment", gated({"uid": 1, "deposit": 1, "prim2": "x", **extra}), ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation"
    assert fake.posted(PAYMENT) == []


async def test_a_payment_needs_the_finance_scope_and_a_reason(settings):
    rt, fake = make_runtime(settings, **{PAYMENT: RECEIPT})
    args = {"uid": 1, "deposit": 1, "prim2": "x"}
    denied = await rt.execute("mb_receive_payment", gated(args), frozenset({"read", "write", "account"}))
    assert denied["error"]["code"] == "scope_denied"
    no_reason = await rt.execute("mb_receive_payment", args, ALL_SCOPE_SET)
    assert no_reason["error"]["code"] == "validation" and "reason" in no_reason["error"]["message"]
    assert fake.posted(PAYMENT) == []


async def test_the_preview_shows_the_subscriber_and_the_total_and_sends_nothing(settings):
    user = {"success": True, "data": {"useruid": 915, "user": "mcpt", "state": "1", "status": "normal",
                                      "deletedtable": 0, "password": "plain"}}
    rt, fake = make_runtime(settings, **{USER: user, PAYMENT: RECEIPT})
    r = await rt.execute("mb_receive_payment", gated({"uid": 915, "deposit": 5, "prim2": "x"}, dry_run=True),
                         ALL_SCOPE_SET)
    assert r["ok"] and r["dry_run"] is True
    assert r["data"]["summary"]["total_amount"] == 5.0
    assert r["data"]["summary"]["targets"][0]["uid"] == 915 and r["data"]["summary"]["targets"][0]["user"] == "mcpt"
    assert r["data"]["request"]["uri"] == PAYMENT and "plain" not in str(r)
    assert fake.posted(PAYMENT) == []


async def test_a_payment_is_audited_without_the_free_text(settings, tmp_path):
    import json

    from mb_mcp.audit import AuditLog
    path = tmp_path / "audit.log"
    rt = Runtime(settings, FakeClient({PAYMENT: RECEIPT}), AuditLog(str(path)))
    await rt.execute("mb_receive_payment", gated({"uid": 915, "deposit": 5, "prim2": "Ivan paid at the desk",
                                                  "beznal_date": "2026-11-15"}), ALL_SCOPE_SET, principal="agent1")
    text = path.read_text(encoding="utf-8")
    assert "Ivan paid" not in text
    events = [json.loads(line) for line in text.splitlines()]
    assert [e["outcome"] for e in events if e.get("tool") == "mb_receive_payment"] == ["attempt", "ok"]
    params = next(e["params"] for e in events if e.get("outcome") == "attempt")
    assert params["deposit"] == 5 and params["uid"] == 915 and params["beznal_date"] == "2026-11-15"


# ---- mb_set_credit ---------------------------------------------------------------------------------------------

async def test_set_credit_sends_uid_and_credit(settings):
    rt, fake = make_runtime(settings, **{CREDIT: {"success": True, "error": 0}})
    r = await rt.execute("mb_set_credit", gated({"uid": 915, "credit": 25}), ALL_SCOPE_SET)
    assert r["ok"] and "dry_run" not in r
    assert fake.posted(CREDIT) == [{"uid": "915", "credit": "25"}]


@pytest.mark.parametrize("credit", [0, -5, "abc", float("inf"), None])
async def test_credit_zero_and_negative_are_refused_because_mikbill_ignores_them_silently(settings, credit):
    rt, fake = make_runtime(settings, **{CREDIT: {"success": True, "error": 0}})
    r = await rt.execute("mb_set_credit", gated({"uid": 915, "credit": credit}), ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation"
    assert fake.posted(CREDIT) == []


async def test_credit_is_capped_by_mb_max_payment_and_needs_the_finance_scope_and_a_reason(settings):
    rt, fake = make_runtime(settings, **{CREDIT: {"success": True, "error": 0}})
    big = await rt.execute("mb_set_credit", gated({"uid": 1, "credit": settings.max_payment * 2}), ALL_SCOPE_SET)
    assert big["error"]["code"] == "validation" and "MB_MAX_PAYMENT" in big["error"]["message"]
    denied = await rt.execute("mb_set_credit", gated({"uid": 1, "credit": 5}), frozenset({"read", "write"}))
    assert denied["error"]["code"] == "scope_denied"
    no_reason = await rt.execute("mb_set_credit", {"uid": 1, "credit": 5}, ALL_SCOPE_SET)
    assert no_reason["error"]["code"] == "validation"
    assert fake.posted(CREDIT) == []


async def test_set_credit_preview_names_the_subscriber(settings):
    user = {"success": True, "data": {"useruid": 915, "user": "mcpt", "state": "1", "status": "normal", "deletedtable": 0}}
    rt, fake = make_runtime(settings, **{USER: user})
    r = await rt.execute("mb_set_credit", gated({"uid": 915, "credit": 25}, dry_run=True), ALL_SCOPE_SET)
    assert r["dry_run"] is True and r["data"]["summary"]["targets"][0]["user"] == "mcpt"
    assert fake.posted(CREDIT) == []


# ---- the error text of /extjs/users/credit ----------------------------------------------------------------------

def test_error_text_is_read_when_the_endpoint_does_not_say_errortext():
    # verified: this endpoint answers {success: false, error: 2, error_text: "credit summa greater than allowed"}
    err = error_from_envelope({"success": False, "error": 2, "error_text": "credit summa greater than allowed"})
    assert err is not None and err.code == "upstream_error" and "greater than allowed" in err.message
    err = error_from_envelope({"success": False, "error": 1001, "error_text": "user not found"})
    assert err is not None and "user not found" in err.message
    err = error_from_envelope({"success": False, "error": 1, "error_text": "parameter 'credit' not found"})
    assert err is not None and "credit" in err.message
    # the usual key still wins
    err = error_from_envelope({"success": False, "error": 1, "errortext": "Denied by settings", "error_text": "x"})
    assert err is not None and err.code == "denied_by_settings"


async def test_an_over_limit_credit_comes_back_as_an_error(settings):
    from mb_mcp.errors import MikbillError
    rt, _ = make_runtime(settings, **{CREDIT: MikbillError("upstream_error", "credit summa greater than allowed")})
    r = await rt.execute("mb_set_credit", gated({"uid": 915, "credit": 2000}), ALL_SCOPE_SET)  # under MB_MAX_PAYMENT, over Mikbill's own maximum
    assert r["ok"] is False and "greater than allowed" in r["error"]["message"]


# ---- mb_list_payment_types and the bughtypeid check -------------------------------------------------------------
# Bug found: an agent could not make a payment because nothing listed the valid bughtypeid values. Mikbill itself
# accepts any value (9999 was recorded as a payment with an empty type) and answers error 1202 when it is missing.

FEE = "/json/users/getpaytypeabonplatalistflex"
PROMO = "/json/users/getpaytypeakciyalistflex"
FIX = "/json/users/getpaytypepereschetlistflex"
CONNECT = "/json/users/getpaytypepodkllistflex"
DEMO_TYPES = {
    FEE: {"success": True, "data": [{"bughtypeid": "1", "typename": "Абонентская плата за текущий месяц"},
                                    {"bughtypeid": "104", "typename": "Пеня"}]},
    PROMO: {"success": True, "data": [{"bughtypeid": "17", "typename": "Акция"}, {"bughtypeid": "16", "typename": "ПОДАРОК!"}]},
    FIX: {"success": True, "data": [{"bughtypeid": "10", "typename": "Перерасчет"},
                                    {"bughtypeid": -1, "typename": "Установить баланс"}]},
    CONNECT: {"success": True, "data": [{"bughtypeid": "27", "typename": "Подключение"}]},
}
OPERATION = {"uid": 5, "bughtypeid": 17, "deposit": 3, "prim2": "bonus"}


def test_the_lookup_is_a_read_tool_with_no_required_arguments(settings):
    ep = by_name["mb_list_payment_types"]
    assert ep.scope == "read" and not ep.gated
    schema = Runtime(settings, FakeClient()).describe(ep)["inputSchema"]
    assert schema["required"] == [] and schema["properties"]["kind"]["enum"] == ["fee", "promotion", "correction", "connection"]
    assert "mb_list_payment_types" in Runtime(settings, FakeClient()).describe(by_name["mb_create_payment"])["description"]
    assert "mb_list_payment_types" in Runtime(settings, FakeClient()).describe(by_name["mb_create_payment"])[
        "inputSchema"]["properties"]["bughtypeid"]["description"]


async def test_the_lookup_reads_all_four_lists_and_labels_each_row(settings):
    rt, fake = make_runtime(settings, **DEMO_TYPES)
    r = await rt.execute("mb_list_payment_types", {}, READ)
    assert r["ok"] and r["totalCount"] == 6
    assert {(t["bughtypeid"], t["kind"]) for t in r["data"]} == {
        (1, "fee"), (104, "fee"), (17, "promotion"), (16, "promotion"), (10, "correction"), (27, "connection")}
    assert next(t for t in r["data"] if t["bughtypeid"] == 17)["typename"] == "Акция"
    assert all(t["bughtypeid"] >= 0 for t in r["data"])  # "set balance" (-1) is left out: ids are 0 or more
    assert [u for u, _ in fake.calls] == [FEE, PROMO, FIX, CONNECT]


async def test_the_lookup_can_be_limited_to_one_kind_and_is_cached(settings):
    rt, fake = make_runtime(settings, **DEMO_TYPES)
    promo = await rt.execute("mb_list_payment_types", {"kind": "promotion"}, READ)
    assert [t["bughtypeid"] for t in promo["data"]] == [17, 16]
    await rt.execute("mb_list_payment_types", {}, READ)
    assert len(fake.calls) == 4  # the second call came from the cache
    bad = await rt.execute("mb_list_payment_types", {"kind": "other"}, READ)
    assert bad["error"]["code"] == "validation"


async def test_the_cache_expires(settings, monkeypatch):
    import mb_mcp.runtime as runtime_module
    rt, fake = make_runtime(settings, **DEMO_TYPES)
    now = [1000.0]
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: now[0])
    await rt.execute("mb_list_payment_types", {}, READ)
    now[0] += runtime_module.PAY_TYPES_TTL + 1
    await rt.execute("mb_list_payment_types", {}, READ)
    assert len(fake.calls) == 8


async def test_an_operation_payment_with_a_listed_type_goes_through(settings):
    rt, fake = make_runtime(settings, **DEMO_TYPES)
    r = await rt.execute("mb_create_payment", gated(OPERATION), ALL_SCOPE_SET)
    assert r["ok"]
    assert fake.posted("/json/users/paymentdoflex") == [{"uid": "5", "bughtypeid": "17", "deposit": "3", "prim2": "bonus"}]


@pytest.mark.parametrize("type_id", [9999, 0, 18, -1])
async def test_an_unknown_type_is_refused_before_anything_is_sent(settings, tmp_path, type_id):
    from mb_mcp.audit import AuditLog
    path = tmp_path / "audit.log"
    fake = FakeClient(DEMO_TYPES)
    rt = Runtime(settings, fake, AuditLog(str(path)))
    r = await rt.execute("mb_create_payment", gated({**OPERATION, "bughtypeid": type_id}), ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation"
    if type_id >= 0:  # -1 fails the id rule before the lookup
        assert "mb_list_payment_types" in r["error"]["message"] and "17 Акция" in r["error"]["message"]
    assert fake.posted("/json/users/paymentdoflex") == []
    assert not path.exists() or path.read_text(encoding="utf-8") == ""  # nothing was recorded as attempted


async def test_the_bulk_payment_checks_the_type_too_and_the_preview_names_it(settings):
    rt, fake = make_runtime(settings, **DEMO_TYPES)
    bulk = {"uids": [1, 2], "bughtypeid": 9999, "deposit": 1, "prim2": "n", "reason": "unit test"}
    r = await rt.execute("mb_bulk_payment", bulk, ALL_SCOPE_SET)
    assert r["error"]["code"] == "validation" and "9999" in r["error"]["message"]
    ok = await rt.execute("mb_bulk_payment", {**bulk, "bughtypeid": 104}, ALL_SCOPE_SET)  # a preview by default
    assert ok["dry_run"] is True
    assert ok["data"]["summary"]["type"] == {"bughtypeid": 104, "typename": "Пеня", "kind": "fee"}
    assert fake.posted("/json/users/paymentdomulti") == []


async def test_a_single_payment_preview_names_the_type(settings):
    rt, _ = make_runtime(settings, **DEMO_TYPES)
    r = await rt.execute("mb_create_payment", gated(OPERATION, dry_run=True), ALL_SCOPE_SET)
    assert r["data"]["summary"]["type"]["typename"] == "Акция" and r["data"]["summary"]["type"]["kind"] == "promotion"


async def test_if_the_types_cannot_be_read_the_payment_is_not_sent(settings):
    from mb_mcp.errors import MikbillError
    rt, fake = make_runtime(settings, **{**DEMO_TYPES, FEE: MikbillError("upstream_error", "boom")})
    r = await rt.execute("mb_create_payment", gated(OPERATION), ALL_SCOPE_SET)
    assert r["ok"] is False and r["error"]["code"] == "upstream_error"
    assert fake.posted("/json/users/paymentdoflex") == []


async def test_a_plain_payment_needs_no_type(settings):
    rt, fake = make_runtime(settings, **{PAYMENT: RECEIPT})
    r = await rt.execute("mb_receive_payment", gated({"uid": 1, "deposit": 1, "prim2": "x"}), ALL_SCOPE_SET)
    assert r["ok"]
    assert [u for u, _ in fake.calls if "getpaytype" in u] == []  # no lookup for the ordinary payment
