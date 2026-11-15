"""Endpoint table for the Mikbill admin MCP server.

Source: upstream kagatan/mb-admin-api (reference only) plus endpoints found in the admin UI
and verified on the public demo (2026-11-15). Every upstream call is POST <uri> with
form-encoded params and a JSON response with a `success` field.

Scopes: read, write (not gated), and the gated scopes finance, account_state, admin.
`admin` holds user groups, the address objects (settlements, neighborhoods, lanes, houses) and the
infrastructure objects (sectors, switches, switch types, device types, IP pools, real IPs, NAS): every
create, edit and delete, all gated. `write` is users, user devices, sticky notes, kick, and tickets
(create, update, notes, status); ticket and ticket note deletes are gated in `account_state`.

Status values:
  verified    executed on the demo with the request fields in fixtures/writes_verified.json
                or fixtures/discovered_and_blockers.json
  blocked     registered; the call returns a mapped error (see SPEC.md, "Blocked and denied")
  denied      registered; the installation settings forbid it
  planned     not executed on the demo yet
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Scope = Literal["read", "write", "finance", "account_state", "admin"]


@dataclass(frozen=True)
class Endpoint:
    tool: str                 # MCP tool name, mb_ prefix
    upstream: str | None      # upstream PHP method, or None when not wrapped upstream
    uri: str                  # upstream URI, always POST
    scope: Scope
    gated: bool = False       # True = dry-run then approval token, requires `reason`
    status: str = "verified"  # verified | blocked | denied | planned
    note: str = ""


# ---- read (22) -------------------------------------------------------------------------

READ: list[Endpoint] = [
    Endpoint("mb_check_session", "checkLoggedIn", "/json/users/getcountfl", "read"),
    Endpoint("mb_count_users", "getCountUsers", "/json/users/getcountfl", "read"),
    Endpoint("mb_search_users", "getUsers", "/json/users/searchflex", "read",
             note="Requires at least one filter (uid). Heavy."),
    Endpoint("mb_get_user", "getUser", "/json/users/getuserdatambp", "read",
             note="Redact password, router_pass, router_wep_pass."),
    Endpoint("mb_get_user_payment_history", "getUsersHistoryPayments", "/json/users/statpaymfl", "read",
             note="uid, from_date, to_date."),
    Endpoint("mb_get_user_short_history", "getUserShortHistory", "/extjs/users/usershorthistory", "read",
             note="Redact postauth pass."),
    Endpoint("mb_get_user_canvas_stat", "getUserCanvasStat", "/json/users/getusercanvasstat", "read",
             note="Upstream marks 'Old'. Redact postauth pass."),
    Endpoint("mb_get_user_devices", "getUserDevicesByUid", "/json/users/getuserdevicesbyuid", "read",
             note="Sent to Mikbill as useruid: with the name uid (as the upstream README does) it returns an empty "
                  "{} even when the user has devices."),
    Endpoint("mb_list_dev_types", "getDevTypesList", "/json/users/getdevtypesfulllist", "read"),
    Endpoint("mb_list_settlements", "getSettlementsList", "/json/users/settlementslist", "read"),
    Endpoint("mb_list_neighborhoods", "getNeighborhoodsList", "/json/users/neighborhoodslist", "read"),
    Endpoint("mb_list_lanes", "getLanesList", "/json/users/getlanesneighborhoods", "read"),
    Endpoint("mb_list_houses", "getUsersHousesFullList", "/json/users/housesfulllist", "read"),
    Endpoint("mb_list_sectors", "getSectorList", "/json/users/sectorlistflex", "read"),
    Endpoint("mb_list_switches", "getSwitchList", "/json/users/switchlistflex", "read"),
    Endpoint("mb_list_switch_types", "getSwitchTypeList", "/json/options/switchtypelistflex", "read"),
    Endpoint("mb_list_packets", "getPacketsList", "/json/tarif/gettarifsfl", "read"),
    Endpoint("mb_list_user_groups", "getUsersGroupsList", "/json/options/usersgroupslist", "read"),
    Endpoint("mb_get_system_options", "getSystemOptions", "/extjs/users/getsystemoptions", "read",
             note="Admin-only data (server paths and settings)."),
    Endpoint("mb_list_tickets", None, "/extjs/ticketview/getticketslist2x", "read",
             note="All tickets with their notes and status counters. Mikbill ignores filters, so the optional "
                  "statustypeid, categoryid, prioritytypeid and ticketid are applied to the rows."),
    Endpoint("mb_get_ticket_notes", None, "/json/ticketview/getnotes", "read", note="ticketid."),
    Endpoint("mb_get_ticket_logs", None, "/json/ticketview/getlogs", "read", note="ticketid."),
    Endpoint("mb_list_ticket_categories", None, "/json/ticketview/getcategorieslist", "read",
             note="Ticket categories: categoryid, maincategoryid, categoryname, description, color and the req_* "
                  "flags for which ticket fields the category requires."),
    Endpoint("mb_list_ticket_priorities", None, "/json/ticketview/getprioritiestypeslist", "read",
             note="Ticket priorities: prioritytypeid, prioritytypename."),
    Endpoint("mb_list_ticket_performers", None, "/extjs/ticketview/getPerformerList", "read",
             note="Staff who can be ticket performers: stuffid, login, fio."),
]

# ---- write (9, not gated) ----------------------------------------------------------------

WRITE: list[Endpoint] = [
    Endpoint("mb_create_user", "createAbonent", "/extjs/users/createabonent", "write",
             note="Needs a sector with free IPs."),
    Endpoint("mb_update_user", "updateUser", "/extjs/users/updateuser", "write", note="Uses useruid."),
    Endpoint("mb_kick_user", "userKickOnline", "/json/users/userkickfl", "write", note="uid."),
    Endpoint("mb_create_sticky_note", "createStickyNotesUser", "/extjs/users/createstickynotesuser", "write",
             note="Use uid (not useruid)."),
    Endpoint("mb_add_user_device", "addUserDev", "/extjs/users/adduserdev", "write"),
    Endpoint("mb_create_ticket", None, "/extjs/ticketedit/setticket2x", "write",
             note="categoryid and prioritytypeid required. The creator is taken from the session. Link a user with "
                  "useruid (the address comes from the user)."),
    Endpoint("mb_update_ticket", None, "/extjs/ticketedit/setticket2x", "write",
             note="Same endpoint as create with ticketid; only the fields you send change."),
    Endpoint("mb_add_ticket_note", None, "/extjs/ticketedit/addnote", "write",
             note="ticketid, note. The author is taken from the session."),
    Endpoint("mb_set_ticket_status", None, "/extjs/ticketedit/putticketin", "write",
             note="status is open, in_work, performed or closed; ticket_ids lists the tickets; rating 1-5 only "
                  "with closed. Uses the acting staff id of the session."),
]

# ---- account_state (13, gated) -----------------------------------------------------------

ACCOUNT_STATE: list[Endpoint] = [
    Endpoint("mb_freeze_user", "setFreezeUser", "/json/users/dofreezeuserfl", "account_state", gated=True),
    Endpoint("mb_bulk_enable_users", None, "/json/users/enableusersmulti", "account_state", gated=True,
             note="set_users_list."),
    Endpoint("mb_bulk_disable_users", None, "/json/users/disableusersmulti", "account_state", gated=True,
             note="set_users_list."),
    Endpoint("mb_bulk_unfreeze_users", None, "/json/users/unfreezeusersmulti", "account_state", gated=True,
             note="set_users_list."),
    Endpoint("mb_bulk_enable_internet", None, "/json/users/enableinternetmulti", "account_state", gated=True,
             note="set_users_list."),
    Endpoint("mb_disconnect_user", None, "/json/users/otkluserflex", "account_state", gated=True,
             note="Sets state 3 (disconnected). Takes uid and cena_freeze."),
    Endpoint("mb_bulk_delete_users", None, "/json/users/deleteusersmulti", "account_state", gated=True,
             note="SOFT delete. set_users_list."),
    Endpoint("mb_delete_user", None, "/json/users/deluserflex", "account_state", gated=True,
             note="SOFT delete: state 4, deletedtable 1. Row remains."),
    Endpoint("mb_delete_disconnected_user", None, "/json/users/deluserotklflex", "account_state", gated=True,
             note="SOFT delete of a disconnected user. uid, gid, itog_summa, type_list=2."),
    Endpoint("mb_delete_user_full", None, "/json/users/delfulluserflex", "account_state", gated=True,
             status="denied", note="'Denied by settings' on the demo. Maps to denied_by_settings."),
    Endpoint("mb_delete_sticky_note", None, "/extjs/users/deletestickynotesuser", "account_state", gated=True,
             note="id."),
    Endpoint("mb_restore_user", None, "/json/users/enableusersmulti", "account_state", gated=True,
             note="Restores a soft-deleted user (state 4). Without gid it uses enableusersmulti and the user keeps "
                  "the old tariff. With gid it uses vkluserflex and also puts the user on that tariff, which can "
                  "charge the tariff's first fee. Only for deleted users."),
    Endpoint("mb_delete_user_device", None, "/extjs/users/deleteuserdev", "account_state", gated=True,
             note="uid (sent as useruid) and devid from mb_get_user_devices."),
    Endpoint("mb_delete_tickets", None, "/extjs/ticketedit/deletetickets", "account_state", gated=True,
             note="ticket_ids; sent as ticketid0, ticketid1, ... The dry-run lists the tickets."),
    Endpoint("mb_delete_ticket_note", None, "/extjs/ticketedit/deletenotes", "account_state", gated=True,
             note="noteid."),
]

# ---- finance (13, gated) -----------------------------------------------------------------

FINANCE: list[Endpoint] = [
    Endpoint("mb_create_payment", "payment", "/json/users/paymentdoflex", "finance", gated=True,
             note="Moves money. uid, bughtypeid, deposit, prim2."),
    Endpoint("mb_bulk_payment", None, "/json/users/paymentdomulti", "finance", gated=True,
             note="Moves money on every listed account. set_users_list, deposit, prim2, bughtypeid."),
    Endpoint("mb_change_tariff", "doChangeTarifFlex", "/json/users/dochangetarifflex", "finance", gated=True,
             note="uid, gid."),
    Endpoint("mb_enable_flex_tariff", "vklUserFlex", "/json/users/vkluserflex", "finance", gated=True,
             note="Reactivates a disconnected user (state 3). Needs uid, gid, itog_summa, type_list=2."),
    Endpoint("mb_add_packet", "addPacket", "/json/tarif/addtarifflex", "finance", gated=True,
             note="tarif (name)."),
    Endpoint("mb_edit_packet", "editPacket", "/json/tarif/updatetariffl", "finance", gated=True,
             note="gid, packet, alias. Other form fields exist."),
    Endpoint("mb_delete_packet", None, "/json/tarif/deltarifflex", "finance", gated=True,
             note="Check first with delchecktarifflex."),
    Endpoint("mb_set_personal_service", "setPersonalServiceUser", "/json/users/setpersonalserviceuser",
             "finance", gated=True,
             status="blocked",
             note="Service must have a configured portal. serviceid 101 (portal 0) works. serviceid 115 (portal 18, Wink) fails with error 27."),
    Endpoint("mb_unset_personal_service", None, "/json/users/unsetpersonalserviceuser",
             "finance", gated=True, note="uid, serviceid (activate, code optional)."),
    Endpoint("mb_remove_credit_single", None, "/json/users/creditnullfl", "finance", gated=True, note="uid."),
    Endpoint("mb_bulk_remove_credit", None, "/json/users/creditnullmulti", "finance", gated=True,
             note="set_users_list."),
    Endpoint("mb_bulk_issue_credit", None, "/json/users/creditmulti", "finance", gated=True,
             note="set_users_list, credit."),
    Endpoint("mb_use_voucher", None, "/json/users/usevaucherfl", "finance", gated=True,
             note="uid, code. Only the wrong-code path was exercised. A valid code adds balance."),
]

# ---- admin (32, gated): user groups, address objects and infrastructure objects --------------

ADMIN: list[Endpoint] = [
    # user groups
    Endpoint("mb_create_user_group", "createUsersGroup", "/json/options/setusersgroup", "admin", gated=True,
             note="usersgroupname, share, description."),
    Endpoint("mb_update_user_group", None, "/json/options/setusersgroup", "admin", gated=True,
             note="Same URI as create. With usersgroupid it updates. Verified."),
    Endpoint("mb_delete_user_group", None, "/json/options/deleteusersgroup", "admin", gated=True,
             note="Use delete_list (the UI param). usersgroupid always returns 'in use'. Dry-run must count users in the group."),
    # address objects: settlements, neighborhoods, lanes, houses
    Endpoint("mb_add_settlement", "addSettlement", "/json/options/addsettlement", "admin", gated=True),
    Endpoint("mb_edit_settlement", "editSettlement", "/json/options/editsettlement", "admin", gated=True),
    Endpoint("mb_delete_settlement", None, "/json/options/deletesettlement", "admin", gated=True,
             note="Check first with checkdeletesettlement. Mikbill does not enforce the check."),
    Endpoint("mb_add_neighborhood", "addNeighborhood", "/json/options/addneighborhood", "admin", gated=True),
    Endpoint("mb_edit_neighborhood", "editNeighborhood", "/json/options/editneighborhood", "admin", gated=True),
    Endpoint("mb_delete_neighborhood", None, "/json/options/deleteneighborhood", "admin", gated=True,
             note="Mikbill does not enforce its dependency check; the dry-run shows it."),
    Endpoint("mb_add_lane", "addLane", "/json/options/addlanesflex", "admin", gated=True),
    Endpoint("mb_edit_lane", "editLane", "/json/options/editlaneflex", "admin", gated=True),
    Endpoint("mb_delete_lane", None, "/json/options/dellaneflex", "admin", gated=True,
             note="Mikbill does not enforce its dependency check; the dry-run shows it."),
    Endpoint("mb_add_house", "addHouse", "/json/options/addhouseflex", "admin", gated=True),
    Endpoint("mb_edit_house", "editHouse", "/json/options/edithouselaneflex", "admin", gated=True),
    Endpoint("mb_delete_house", "deleteHouse", "/json/options/delhouseflex", "admin", gated=True,
             status="blocked",
             note="Send houseid only. Error 8 means users still reference the house. delete_list reports success but deletes nothing."),
    # sectors
    Endpoint("mb_add_sector", "addSector", "/json/options/sectoraddflex", "admin", gated=True),
    Endpoint("mb_edit_sector", "editSector", "/json/options/sectoreditflex", "admin", gated=True),
    Endpoint("mb_delete_sector", None, "/json/options/sectordelflex", "admin", gated=True),
    # switch types and switches
    Endpoint("mb_add_switch_type", "addSwitchType", "/json/options/addswitchtypeflex", "admin", gated=True,
             note="Upstream wrapper calls it without params. With params it works."),
    Endpoint("mb_edit_switch_type", "editSwitchType", "/json/options/editswithchtypeflex", "admin", gated=True,
             note="Upstream URI spelling kept."),
    Endpoint("mb_delete_switch_type", "delSwitchType", "/json/options/delswitchtypeflex", "admin", gated=True),
    Endpoint("mb_add_switch", "addSwitch", "/json/options/addswitchflex", "admin", gated=True,
             note="Upstream wrapper calls it without params. With params it works."),
    Endpoint("mb_edit_switch", "editSwitch", "/json/options/editswitchflex", "admin", gated=True),
    Endpoint("mb_delete_switch", "delSwitch", "/json/options/delswitchflex", "admin", gated=True),
    # device types
    Endpoint("mb_add_dev_type", "addDevType", "/json/users/adddevtype", "admin", gated=True,
             note="Requires devtypename. Calling with no params still returns success."),
    Endpoint("mb_delete_dev_type", None, "/json/users/deletedevtype", "admin", gated=True,
             note="devtypeid, sent as delete_list. With a plain devtypeid Mikbill answers success and deletes "
                  "nothing. A type still used by a device is refused."),
    # IP pools, real IPs, NAS
    Endpoint("mb_add_ip_pool", None, "/json/options/addippoolsflex", "admin", gated=True,
             note="poolname. Verified (created MCPTEST_POOL)."),
    Endpoint("mb_delete_ip_pool", None, "/json/options/delippoolsflex", "admin", gated=True,
             note="poolname."),
    Endpoint("mb_add_real_ip_block", None, "/json/options/addrealipflex", "admin", gated=True,
             note="localip, netmask, cidrmask, hostmin, hostmax, hosts. Verified (created 254 addresses)."),
    Endpoint("mb_delete_real_ip", None, "/json/options/delrealipflex", "admin", gated=True,
             note="One address per call (real)."),
    Endpoint("mb_add_nas", None, "/json/options/nasaddflex", "admin", gated=True,
             note="nasname, nasip, secret, shortname. Verified (created MCPTEST_NAS)."),
    Endpoint("mb_delete_nas", None, "/json/options/nasserverdelflex", "admin", gated=True,
             note="nasid."),
]

ALL: list[Endpoint] = READ + WRITE + ACCOUNT_STATE + FINANCE + ADMIN

# Excluded from v1 entirely (not registered in any scope):
EXCLUDED: list[tuple[str, str]] = [
    ("consoleMe", "Arbitrary console execution on the billing side."),
    ("/extjs/options/consolecommands", "Console execution."),
    ("auth", "Client internal (login)."),
    ("authBilling", "Client internal (login)."),
]

# Discovered, not yet verified or not decided. Not registered:
PENDING: list[tuple[str, str]] = [
    ("/json/options/delippoolsipflex", "Returned {} for a bogus poolip. Needs valid input."),
    ("/json/users/nagrasyncsubscriptions", "External Nagra integration. Not called."),
]
