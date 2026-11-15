"""Live contract tests: the verified sequence through the MCP runtime against the public Mikbill demo.

OPT-IN. These tests WRITE to the target Mikbill. They are skipped unless all of this is set:

    MB_CONTRACT=1
    MB_CONTRACT_CONFIRM=I_ACCEPT_WRITES_ON_DEMO
    MB_BASE_URL, MB_LOGIN, MB_PASSWORD        (the demo account; never commit them)

Safety rules built in:
- The target host must be in MB_CONTRACT_ALLOWED_HOSTS (default: admin.mikbill.pro). Anything else is refused.
- Every record this suite creates is named MCPTEST_<run id>... and is deleted again where Mikbill allows.
- Users are created in an EXISTING house. Mikbill soft-deletes users, so each run leaves a few soft-deleted
  MCPTEST users behind (state 4). Houses are never deleted while users reference them.
- Gated tools are always run as dry-run first, and the dry-run is checked to change nothing.

Optional overrides for other installations: MB_CONTRACT_SECTOR_ID (a sector with free IPs, default 13),
MB_CONTRACT_GID (packet id for new users, default 1), MB_CONTRACT_VKL_GID (default 49),
MB_CONTRACT_SERVICE_ID (a personal service without a portal, default 101).
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import pytest
import pytest_asyncio

from mb_mcp.client import MikbillClient
from mb_mcp.config import ALL_SCOPES, Settings
from mb_mcp.runtime import Runtime

pytestmark = [pytest.mark.contract, pytest.mark.asyncio(loop_scope="module")]

ALL = frozenset(ALL_SCOPES)
REASON = "contract test"


def _guard() -> None:
    if os.environ.get("MB_CONTRACT") != "1":
        pytest.skip("live contract tests are opt-in: set MB_CONTRACT=1")
    if os.environ.get("MB_CONTRACT_CONFIRM") != "I_ACCEPT_WRITES_ON_DEMO":
        pytest.skip("set MB_CONTRACT_CONFIRM=I_ACCEPT_WRITES_ON_DEMO to allow writes")
    base = os.environ.get("MB_BASE_URL", "")
    allowed = {h.strip() for h in os.environ.get("MB_CONTRACT_ALLOWED_HOSTS", "admin.mikbill.pro").split(",") if h.strip()}
    host = urlparse(base).hostname or ""
    if host not in allowed:
        pytest.skip(f"refusing to write to {host!r}: not in MB_CONTRACT_ALLOWED_HOSTS {sorted(allowed)}")
    for name in ("MB_LOGIN", "MB_PASSWORD"):
        if not os.environ.get(name):
            pytest.skip(f"{name} is not set")


def find(data: Any, key: str) -> Any:
    """First value for `key` anywhere in nested dicts/lists, or None."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for v in data.values():
            r = find(v, key)
            if r is not None:
                return r
    elif isinstance(data, list):
        for v in data:
            r = find(v, key)
            if r is not None:
                return r
    return None


class Demo:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.run = time.strftime("%m%d%H%M%S")
        self._cleanup: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
        self.failures: list[str] = []
        self.leftovers: list[str] = []

    def name(self, suffix: str) -> str:
        return f"MCPTEST_{self.run}_{suffix}"

    def login(self, suffix: str) -> str:
        return f"mcpt{self.run}{suffix}"

    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        return await self.rt.execute(tool, args, ALL, "contract")

    async def ok(self, tool: str, **args: Any) -> dict[str, Any]:
        r = await self.call(tool, **args)
        assert r["ok"], f"{tool} failed: {r}"
        return r

    async def err(self, tool: str, code: str, **args: Any) -> dict[str, Any]:
        r = await self.call(tool, **args)
        assert not r["ok"] and r["error"]["code"] == code, f"{tool}: expected {code}, got {r}"
        return r

    async def gated(self, tool: str, **args: Any) -> dict[str, Any]:
        """Dry-run, then execute with the token. Returns the executed result."""
        dry = await self.ok(tool, reason=REASON, **args)
        assert dry["dry_run"] is True and dry["data"]["approval_token"], f"{tool}: no dry-run preview"
        return await self.call(tool, reason=REASON, dry_run=False, approval_token=dry["data"]["approval_token"], **args)

    async def gated_ok(self, tool: str, **args: Any) -> dict[str, Any]:
        r = await self.gated(tool, **args)
        assert r["ok"], f"{tool} failed: {r}"
        return r

    async def user(self, uid: int) -> dict[str, Any]:
        return (await self.ok("mb_get_user", uid=uid))["data"]

    # ---- cleanup -------------------------------------------------------------------------

    def defer(self, label: str, fn: Callable[[], Awaitable[Any]]) -> None:
        self._cleanup.append((label, fn))

    def done(self, label: str) -> None:
        self._cleanup = [(l, f) for l, f in self._cleanup if l != label]

    async def run_cleanup(self) -> None:
        for label, fn in reversed(self._cleanup):
            try:
                r = await fn()
                if isinstance(r, dict) and not r.get("ok", True):
                    self.failures.append(f"cleanup {label}: {r}")
            except Exception as exc:  # noqa: BLE001 - cleanup must not stop the rest
                self.failures.append(f"cleanup {label}: {exc!r}")
        self._cleanup = []


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def demo():
    _guard()
    settings = replace(Settings.from_env(), scopes=ALL)
    client = MikbillClient(settings)
    d = Demo(Runtime(settings, client))
    try:
        yield d
    finally:
        await d.run_cleanup()
        await client.aclose()
    if d.failures:
        print("\nCLEANUP PROBLEMS (records may be left on the target):\n  " + "\n  ".join(d.failures))
    if d.leftovers:
        print("\nLEFT ON THE TARGET (not a failure):\n  " + "\n  ".join(d.leftovers))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def house(demo):
    """An existing house to put test users in (no houses are created for users)."""
    houses = [h for h in (await demo.ok("mb_list_houses"))["data"] if not str(h["house"]).startswith("MCPTEST")]
    assert houses, "the target has no houses (other than MCPTEST ones)"
    h = houses[0]
    return {k: int(h[k] or 0) for k in ("houseid", "laneid", "neighborhoodid", "settlementid")}


async def new_user(d: Demo, house: dict[str, int], suffix: str) -> int:
    r = await d.ok(
        "mb_create_user", user=d.login(suffix), password=f"MCPTEST_pw_{d.run}{suffix}", fio=d.name("user" + suffix),
        gid=int(os.environ.get("MB_CONTRACT_GID", "1")), sectorid=int(os.environ.get("MB_CONTRACT_SECTOR_ID", "13")),
        app="1", **house)
    uid = int(find(r["data"], "useruid"))

    async def soft_delete():
        return await d.gated("mb_delete_user", uid=uid)

    d.defer(f"user {uid}", soft_delete)
    return uid


# ---- reads ----------------------------------------------------------------------------------

async def test_read_tools(demo):
    assert (await demo.ok("mb_check_session"))["data"] == {"logged_in": True}
    assert (await demo.ok("mb_count_users"))["data"]
    for tool in ("mb_list_dev_types", "mb_list_settlements", "mb_list_neighborhoods", "mb_list_lanes", "mb_list_houses",
                 "mb_list_sectors", "mb_list_switches", "mb_list_switch_types", "mb_list_packets", "mb_list_user_groups"):
        data = (await demo.ok(tool))["data"]
        assert isinstance(data, list) and data, f"{tool} returned no rows"
    assert (await demo.ok("mb_get_system_options"))["data"]


async def test_scope_and_dry_run_guards_are_enforced_live(demo):
    r = await demo.rt.execute("mb_delete_user", {"uid": 1, "reason": REASON}, frozenset({"read", "write"}), "contract")
    assert r["error"]["code"] == "scope_denied"
    await demo.err("mb_delete_user", "validation", uid=1)  # reason is required
    await demo.err("mb_delete_user", "approval_required", uid=1, reason=REASON, dry_run=False)


# ---- reference data -------------------------------------------------------------------------

async def test_reference_data_crud(demo):
    s_name = demo.name("S")
    r = await demo.ok("mb_add_settlement", settlementname=s_name)
    sid = int(find(r["data"], "settlementid"))
    demo.defer(f"settlement {sid}", lambda: demo.gated("mb_delete_settlement", settlementid=sid))
    await demo.ok("mb_edit_settlement", settlementid=sid, settlementname=s_name + "b")
    assert any(int(x["settlementid"]) == sid and x["settlementname"].endswith("b")
               for x in (await demo.ok("mb_list_settlements"))["data"])

    r = await demo.ok("mb_add_neighborhood", settlementid=sid, neighborhoodname=demo.name("N"))
    nid = int(find(r["data"], "neighborhoodid"))
    demo.defer(f"neighborhood {nid}", lambda: demo.gated("mb_delete_neighborhood", neighborhoodid=nid))
    await demo.ok("mb_edit_neighborhood", settlementid=sid, neighborhoodid=nid, neighborhoodname=demo.name("Nb"))

    r = await demo.ok("mb_add_lane", settlementid=sid, lane=demo.name("L"))
    lid = int(find(r["data"], "laneid"))
    demo.defer(f"lane {lid}", lambda: demo.gated("mb_delete_lane", laneid=lid))
    await demo.ok("mb_edit_lane", settlementid=sid, laneid=lid, lane=demo.name("Lb"))

    r = await demo.ok("mb_add_house", house=demo.name("H"), laneid=lid, neighborhoodid=nid, porches=1, floors=1,
                      app_count_id="1", postal_index="00000")
    hid = int(find(r["data"], "houseid"))
    demo.defer(f"house {hid}", lambda: demo.gated("mb_delete_house", houseid=hid))
    await demo.ok("mb_edit_house", houseid=hid, house=demo.name("Hb"), laneid=lid, neighborhoodid=nid, porches=2, floors=2)

    r = await demo.ok("mb_add_sector", sector=demo.name("SEC"))
    secid = int(find(r["data"], "sectorid"))
    demo.defer(f"sector {secid}", lambda: demo.gated("mb_delete_sector", sectorid=secid))
    await demo.ok("mb_edit_sector", sectorid=secid, sector=demo.name("SECb"))

    # deletes, children first. The house has no users, so Mikbill's in-use check passes.
    await demo.gated_ok("mb_delete_house", houseid=hid); demo.done(f"house {hid}")
    await demo.gated_ok("mb_delete_lane", laneid=lid); demo.done(f"lane {lid}")
    await demo.gated_ok("mb_delete_neighborhood", neighborhoodid=nid); demo.done(f"neighborhood {nid}")
    dry = await demo.ok("mb_delete_settlement", settlementid=sid, reason=REASON)
    assert dry["data"]["summary"]["check"]["ok"] is True
    await demo.gated_ok("mb_delete_settlement", settlementid=sid); demo.done(f"settlement {sid}")
    await demo.gated_ok("mb_delete_sector", sectorid=secid); demo.done(f"sector {secid}")
    assert not any(int(x["settlementid"]) == sid for x in (await demo.ok("mb_list_settlements"))["data"])


# ---- users ----------------------------------------------------------------------------------

async def test_user_lifecycle_and_finance(demo, house):
    uid = await new_user(demo, house, "a")

    u = await demo.user(uid)
    assert u["user"] == demo.login("a")
    assert u["password"] != f"MCPTEST_pw_{demo.run}a", "plaintext password was not redacted"
    found = (await demo.ok("mb_search_users", uid=uid))["data"]
    assert any(int(x["useruid"]) == uid for x in found)

    await demo.ok("mb_update_user", useruid=uid, user=demo.login("a"), fio=demo.name("usera2"), prim="MCPTEST upd")
    await demo.ok("mb_kick_user", uid=uid)

    note = await demo.ok("mb_create_sticky_note", uid=uid, description="MCPTEST note")
    note_id = int(find(note["data"], "id"))
    await demo.gated_ok("mb_delete_sticky_note", id=note_id)

    # payment: the dry-run changes nothing, the executed call adds the amount
    before = float((await demo.user(uid))["deposit"])
    pay = dict(uid=uid, bughtypeid=17, deposit=10, prim2="MCPTEST pay")
    dry = await demo.ok("mb_create_payment", reason=REASON, **pay)
    assert dry["data"]["summary"]["total_amount"] == 10.0
    assert float((await demo.user(uid))["deposit"]) == before, "dry-run changed the balance"
    await demo.gated_ok("mb_create_payment", **pay)
    assert float((await demo.user(uid))["deposit"]) == before + 10
    hist = (await demo.ok("mb_get_user_payment_history", uid=uid))["data"]
    assert isinstance(hist, list) and hist

    await demo.gated_ok("mb_bulk_payment", uids=[uid], bughtypeid=17, deposit=5, prim2="MCPTEST bulk")
    assert float((await demo.user(uid))["deposit"]) == before + 15
    await demo.gated_ok("mb_bulk_issue_credit", uids=[uid], credit=100)
    await demo.gated_ok("mb_bulk_remove_credit", uids=[uid])
    await demo.gated_ok("mb_remove_credit_single", uid=uid)

    # account state
    # Mikbill enforces a minimum freeze length (the user's min_sutok_freeze days); use that plus a margin.
    min_days = int(float((await demo.user(uid)).get("min_sutok_freeze") or 0))
    start = date.today()
    stop = start + timedelta(days=max(min_days, 1) + 1)
    await demo.gated_ok("mb_freeze_user", uid=uid, do_freeze_now=1, freeze_date_start=start.isoformat(),
                        freeze_date_stop=stop.isoformat())
    await demo.gated_ok("mb_bulk_unfreeze_users", uids=[uid])
    await demo.gated_ok("mb_bulk_disable_users", uids=[uid])
    await demo.gated_ok("mb_bulk_enable_users", uids=[uid])
    await demo.gated_ok("mb_bulk_enable_internet", uids=[uid])

    # personal service (a service without a portal; Wink-portal services fail with precondition_failed)
    sid = int(os.environ.get("MB_CONTRACT_SERVICE_ID", "101"))
    await demo.gated_ok("mb_set_personal_service", uid=uid, serviceid=sid)
    await demo.gated_ok("mb_unset_personal_service", uid=uid, serviceid=sid)

    # A tariff change is scheduled, not applied: the user's gid reads 0 until it takes effect, and Mikbill then
    # rejects personal-service calls ("service_portal ... not found"). So this runs after the service calls.
    await demo.gated_ok("mb_change_tariff", uid=uid, gid=2)
    r = await demo.gated("mb_set_personal_service", uid=uid, serviceid=sid)
    assert not r["ok"] and r["error"]["code"] == "precondition_failed", r

    # a wrong voucher is rejected and changes nothing
    bal = float((await demo.user(uid))["deposit"])
    r = await demo.gated("mb_use_voucher", uid=uid, code="MCPTEST-NO-SUCH-CODE")
    assert not r["ok"] and r["error"]["code"] == "upstream_error"
    assert float((await demo.user(uid))["deposit"]) == bal

    # soft delete: the account stays, state 4
    r = await demo.gated_ok("mb_delete_user", uid=uid); demo.done(f"user {uid}")
    assert r["state_after"][0]["status"] == "deleted" and int(r["state_after"][0]["deletedtable"]) == 1


async def test_disconnect_reactivate_and_delete_paths(demo, house):
    # B: disconnect (state 3) -> reactivate on a VKL tariff (state 1) -> disconnect -> delete as disconnected
    b = await new_user(demo, house, "b")
    r = await demo.gated_ok("mb_disconnect_user", uid=b)
    assert str(r["state_after"][0]["state"]) == "3"
    vkl_gid = int(os.environ.get("MB_CONTRACT_VKL_GID", "49"))
    await demo.gated_ok("mb_enable_flex_tariff", uid=b, gid=vkl_gid)
    assert str((await demo.user(b))["state"]) == "1"
    await demo.gated_ok("mb_disconnect_user", uid=b)
    r = await demo.gated_ok("mb_delete_disconnected_user", uid=b, gid=vkl_gid); demo.done(f"user {b}")
    assert r["state_after"][0]["status"] == "deleted"

    # C: bulk soft delete
    c = await new_user(demo, house, "c")
    dry = await demo.ok("mb_bulk_delete_users", uids=[c], reason=REASON)
    assert [t["uid"] for t in dry["data"]["summary"]["targets"]] == [c]
    assert str((await demo.user(c))["state"]) != "4", "dry-run deleted the user"
    r = await demo.gated_ok("mb_bulk_delete_users", uids=[c]); demo.done(f"user {c}")
    assert r["state_after"][0]["status"] == "deleted"

    # full delete is forbidden by the demo's settings
    d = await new_user(demo, house, "d")
    r = await demo.gated("mb_delete_user_full", uid=d)
    assert not r["ok"] and r["error"]["code"] == "denied_by_settings", r


# ---- catalogs, network and admin objects ------------------------------------------------------

async def test_packet_device_type_group_and_network_objects(demo):
    # packet
    name = demo.name("PKT")
    await demo.gated_ok("mb_add_packet", tarif=name)
    gid = next(int(p["gid"]) for p in (await demo.ok("mb_list_packets"))["data"] if p["packet"] == name)
    demo.defer(f"packet {gid}", lambda: demo.gated("mb_delete_packet", gid=gid))
    await demo.gated_ok("mb_edit_packet", gid=gid, packet=name + "b", alias="MCPTEST")
    dry = await demo.ok("mb_delete_packet", gid=gid, reason=REASON)
    assert dry["data"]["summary"]["check"]["ok"] is True
    await demo.gated_ok("mb_delete_packet", gid=gid); demo.done(f"packet {gid}")

    # device type
    name = demo.name("DT")
    await demo.ok("mb_add_dev_type", devtypename=name, type=0, default_cost=0, default_rent=0, default_owner=0,
                  description="MCPTEST")
    dtid = next(int(t["devtypeid"]) for t in (await demo.ok("mb_list_dev_types"))["data"] if t["devtypename"] == name)
    demo.defer(f"devtype {dtid}", lambda: demo.gated("mb_delete_dev_type", devtypeid=dtid))
    await demo.gated_ok("mb_delete_dev_type", devtypeid=dtid); demo.done(f"devtype {dtid}")

    # user group: create, update, delete with delete_list
    r = await demo.gated_ok("mb_create_user_group", usersgroupname=demo.name("G"), description="MCPTEST")
    gid = int(find(r["data"], "usersgroupid"))
    demo.defer(f"group {gid}", lambda: demo.gated("mb_delete_user_group", usersgroupid=gid))
    await demo.gated_ok("mb_update_user_group", usersgroupid=gid, usersgroupname=demo.name("Gb"))
    r = await demo.gated("mb_delete_user_group", usersgroupid=gid)
    demo.done(f"group {gid}")
    if r["ok"]:
        assert not any(int(g["usersgroupid"]) == gid for g in (await demo.ok("mb_list_user_groups"))["data"])
    else:
        # Mikbill reuses group ids, and an old (deleted) subscriber can still reference the new id: error 3, "in use".
        # That is the target's data, not a tool bug, so it is reported as a leftover instead of failing the run.
        assert r["error"]["code"] == "conflict", r
        demo.leftovers.append(f"user group {gid}: still referenced by an old subscriber on the target")

    # IP pool
    pool = demo.name("POOL")
    await demo.gated_ok("mb_add_ip_pool", poolname=pool)
    demo.defer(f"pool {pool}", lambda: demo.gated("mb_delete_ip_pool", poolname=pool))
    await demo.gated_ok("mb_delete_ip_pool", poolname=pool); demo.done(f"pool {pool}")

    # real IP block of four addresses, deleted one by one
    base = "10.99.98"
    await demo.gated_ok("mb_add_real_ip_block", localip=f"{base}.0", netmask="255.255.255.252", cidrmask=30,
                        hostmin=f"{base}.1", hostmax=f"{base}.2", hosts=2)
    for host in (1, 2):
        await demo.gated_ok("mb_delete_real_ip", real=f"{base}.{host}")

    # NAS
    r = await demo.gated_ok("mb_add_nas", nasname=demo.name("NAS"), nasip="10.99.97.1", secret="MCPTEST",
                            shortname=demo.name("NAS"))
    nasid = int(find(r["data"], "nasid"))
    demo.defer(f"nas {nasid}", lambda: demo.gated("mb_delete_nas", nasid=nasid))
    await demo.gated_ok("mb_delete_nas", nasid=nasid); demo.done(f"nas {nasid}")

    # switch type and switch
    r = await demo.ok("mb_add_switch_type", swtypename=demo.name("ST"), devicetypeid=1, numports=8, snmp_type=0,
                      montype=0, cisco=0)
    stid = int(find(r["data"], "swtypeid"))
    demo.defer(f"switch type {stid}", lambda: demo.gated("mb_delete_switch_type", swtypeid=stid))
    await demo.ok("mb_edit_switch_type", swtypeid=stid, swtypename=demo.name("STb"), devicetypeid=1, numports=12)
    r = await demo.ok("mb_add_switch", nameswitch=demo.name("SW"), swtypeid=stid, ip="10.99.99.1",
                      mac="00:00:00:00:99:01", address="MCPTEST")
    swid = int(find(r["data"], "swid"))
    demo.defer(f"switch {swid}", lambda: demo.gated("mb_delete_switch", swid=swid))
    await demo.ok("mb_edit_switch", swid=swid, nameswitch=demo.name("SWb"), swtypeid=stid, ip="10.99.99.2")
    await demo.gated_ok("mb_delete_switch", swid=swid); demo.done(f"switch {swid}")
    await demo.gated_ok("mb_delete_switch_type", swtypeid=stid); demo.done(f"switch type {stid}")


async def test_cleanup_left_nothing_pending(demo):
    """Runs last: anything still deferred was not deleted by the test body (the fixture removes it afterwards)."""
    await demo.run_cleanup()
    assert not demo.failures, "\n".join(demo.failures)
