"""Endpoint table for the Mikbill admin MCP server.

Source: upstream kagatan/mb-admin-api (reference only) plus endpoints found in the admin UI
and verified on the public demo (2026-11-15). Every upstream call is POST <uri> with
form-encoded params and a JSON response with a `success` field.

Scopes: read, write (not gated), and the gated scopes finance, account, admin.
`admin` holds user groups, the address objects (settlements, neighborhoods, lanes, houses) and the
infrastructure objects (sectors, switches, switch types, device types, IP pools, real IPs, NAS): every
create, edit and delete, all gated. `admin` also holds the read-only mb_get_system_options. `account` also holds creating a subscriber. `write` is editing users, user devices,
sticky notes, kick, and tickets (create, update, notes, status), and the deletes of sticky notes, user devices,
tickets and ticket notes. mb_delete_tickets cannot be undone, so it is gated inside `write`: reason, preview by default.

Each endpoint has two texts: `desc` is the tool description a model reads, `note` is for maintainers.

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

Scope = Literal["read", "write", "finance", "account", "admin"]


@dataclass(frozen=True)
class Endpoint:
    tool: str                 # MCP tool name, mb_ prefix
    upstream: str | None      # upstream PHP method, or None when not wrapped upstream
    uri: str                  # upstream URI, always POST
    scope: Scope
    gated: bool = False       # opt-in scope: requires `reason`; bulk tools preview by default; the client
                              # (harness) asks the user to approve the call, see tool_annotations()
    status: str = "verified"  # verified | blocked | denied | planned
    note: str = ""            # for maintainers: how the endpoint behaves and what was verified. Never shown to a model.
    desc: str = ""            # the tool description a model reads: what the tool does, in one or two sentences
    read_only: bool = False   # a tool outside the read scope that changes nothing (mb_get_system_options): no preview,
                              # no intent record, readOnlyHint; it can still be gated (scope and reason)

    @property
    def is_read(self) -> bool:
        """True when the tool only reads: no intent record, no reason, readOnlyHint."""
        return self.scope == "read" or self.read_only


# ---- read (26) -------------------------------------------------------------------------

READ: list[Endpoint] = [
    Endpoint("mb_check_session", "checkLoggedIn", "/json/users/getcountfl", "read",
             desc="Check that the server can log in to Mikbill."),
    Endpoint("mb_count_users", "getCountUsers", "/json/users/getcountfl", "read",
             desc="Count subscribers."),
    Endpoint("mb_search_users", "getUsers", "/json/users/searchflex", "read",
             note="Search subscribers with the filters of the admin UI's search tab: uid, text (login/name/note, "
                  "address, phone/e-mail, IP/MAC/serial, sticky note, apartment), place (settlement, neighborhood, "
                  "lane, house), tariff, switch, sector, port, contract state, statuses, labels and internet on/off. "
                  "At least one filter is required. Filters combine with match all (default) or any (not with states or internet). Deleted "
                  "subscribers are included unless states is given. Returns at most limit rows (default 100); "
                  "totalCount is the full number.",
             desc="Search subscribers with the filters of the admin UI's search tab: uid, text (login/name/note, address, phone/e-mail, IP/MAC/serial, sticky note, apartment), place, tariff, switch, sector, port, contract state, statuses, labels and internet on/off. At least one filter is required. Deleted subscribers are included unless states is given. Returns a short row per subscriber (detail full for the whole record) and at most limit rows; totalCount is the full number."),
    Endpoint("mb_list_online_users", None, "/json/users/inetonlinefl", "read",
             note="Subscribers with an active internet session right now: login, IP, session start and duration, "
                  "traffic counters, tariff, switch and address. Mikbill ignores filters for this endpoint, so the "
                  "optional useruid, user, gid, sectorid, swid and settlementid are applied to the rows.",
             desc="List subscribers with an active internet session right now: login, IP, session start and duration, traffic counters, tariff, switch and address. The optional filters are applied to the rows."),
    Endpoint("mb_get_payment_info", None, "/json/users/howmatchpayflex", "read",
             note="The data behind the admin UI's Payment window: itog_summa (recommended payment), the fee parts "
                  "(abonplata, abonplata_installments, abonplata_real_now, abonplata_subscriptions, abonplata_user_dev), "
                  "date_itog, days_left, deposit, total_payments. Without uid Mikbill answers error 25.",
             desc="Show what a subscriber should pay: the recommended payment, the parts of the fee (tariff, "
                  "installments, real IP, subscriptions, devices), the date the service is paid until, days left, the "
                  "balance and total payments so far."),
    Endpoint("mb_list_payment_types", None, "/json/users/getpaytypeabonplatalistflex", "read",
             note="Reads the four lists the UI's operation forms use: getpaytypeabonplatalistflex (fee), "
                  "getpaytypeakciyalistflex (promotion), getpaytypepereschetlistflex (correction) and "
                  "getpaytypepodkllistflex (connection). Each row is bughtypeid and typename (Russian). 'Set balance' "
                  "(-1) is left out: bughtypeid must be 0 or more. The uri here is the first list; the runtime reads "
                  "all four. Cached for five minutes.",
             desc="List the payment (operation) types, with the bughtypeid that mb_create_payment and mb_bulk_payment "
                  "need, grouped as fee (charges such as subscription fees and penalties), promotion (bonuses and "
                  "gifts), correction (recalculations and withdrawals) and connection (connection and repair work). "
                  "The ids differ between installations. A plain payment from a subscriber needs no type: use "
                  "mb_receive_payment."),
    Endpoint("mb_get_user", "getUser", "/json/users/getuserdatambp", "read",
             note="Redact password, router_pass, router_wep_pass.",
             desc="Get one subscriber's full record: account state, tariff, balance, address, contacts and network settings. Passwords are always masked; passport, tax id and birth date are masked unless the pii scope is granted."),
    Endpoint("mb_get_user_payment_history", "getUsersHistoryPayments", "/json/users/statpaymfl", "read",
             note="uid, from_date, to_date.",
             desc="List a subscriber's payments, optionally within a period."),
    Endpoint("mb_get_user_short_history", "getUserShortHistory", "/extjs/users/usershorthistory", "read",
             note="Redact postauth pass.",
             desc="Get a subscriber's recent history: sessions, authorizations and account events."),
    Endpoint("mb_get_user_canvas_stat", "getUserCanvasStat", "/json/users/getusercanvasstat", "read",
             note="Upstream marks 'Old'. Redact postauth pass.",
             desc="Get a subscriber's traffic statistics as chart data."),
    Endpoint("mb_get_user_devices", "getUserDevicesByUid", "/json/users/getuserdevicesbyuid", "read",
             note="Sent to Mikbill as useruid: with the name uid (as the upstream README does) it returns an empty "
                  "{} even when the user has devices.",
             desc="List the devices assigned to a subscriber (devid, type, MAC, serial)."),
    Endpoint("mb_list_dev_types", "getDevTypesList", "/json/users/getdevtypesfulllist", "read",
             desc="List device types (devtypeid, name, default price and rent)."),
    Endpoint("mb_list_settlements", "getSettlementsList", "/json/users/settlementslist", "read",
             desc="List settlements (settlementid, name)."),
    Endpoint("mb_list_neighborhoods", "getNeighborhoodsList", "/json/users/neighborhoodslist", "read",
             desc="List neighborhoods (neighborhoodid, name, settlement)."),
    Endpoint("mb_list_lanes", "getLanesList", "/json/users/getlanesneighborhoods", "read",
             desc="List lanes (streets) with their settlement."),
    Endpoint("mb_list_houses", "getUsersHousesFullList", "/json/users/housesfulllist", "read",
             desc="List houses with their lane, neighborhood and settlement."),
    Endpoint("mb_list_sectors", "getSectorList", "/json/users/sectorlistflex", "read",
             desc="List sectors (network segments)."),
    Endpoint("mb_list_switches", "getSwitchList", "/json/users/switchlistflex", "read",
             desc="List switches (swid, name, type, IP, MAC, address)."),
    Endpoint("mb_list_switch_types", "getSwitchTypeList", "/json/options/switchtypelistflex", "read",
             desc="List switch types (swtypeid, name, number of ports)."),
    Endpoint("mb_list_packets", "getPacketsList", "/json/tarif/gettarifsfl", "read",
             desc="List tariffs (packets): gid, name and prices."),
    Endpoint("mb_list_user_groups", "getUsersGroupsList", "/json/options/usersgroupslist", "read",
             desc="List user groups (usersgroupid, name)."),
    Endpoint("mb_list_tickets", None, "/extjs/ticketview/getticketslist2x", "read",
             note="All tickets with their notes and status counters. Mikbill ignores filters, so the optional "
                  "statustypeid, categoryid, prioritytypeid and ticketid are applied to the rows.",
             desc="List tickets with their notes and the status, priority and category counters. The optional filters are applied to the rows; counters are unfiltered. Returns at most limit rows."),
    Endpoint("mb_get_ticket_notes", None, "/json/ticketview/getnotes", "read", note="ticketid.",
             desc="List the notes of one ticket."),
    Endpoint("mb_get_ticket_logs", None, "/json/ticketview/getlogs", "read", note="ticketid.",
             desc="List the change log of one ticket."),
    Endpoint("mb_list_ticket_categories", None, "/json/ticketview/getcategorieslist", "read",
             note="Ticket categories: categoryid, maincategoryid, categoryname, description, color and the req_* "
                  "flags for which ticket fields the category requires.",
             desc="List ticket categories: categoryid, name, color and which ticket fields each category requires."),
    Endpoint("mb_list_ticket_priorities", None, "/json/ticketview/getprioritiestypeslist", "read",
             note="Ticket priorities: prioritytypeid, prioritytypename.",
             desc="List ticket priorities (prioritytypeid, name)."),
    Endpoint("mb_list_ticket_performers", None, "/extjs/ticketview/getPerformerList", "read",
             note="Staff who can be ticket performers: stuffid, login, fio.",
             desc="List staff who can be ticket performers (stuffid, login, name)."),
]

# ---- write (12; only mb_delete_tickets is gated) --------------------------------------------

WRITE: list[Endpoint] = [
    Endpoint("mb_update_user", "updateUser", "/extjs/users/updateuser", "write", note="Uses useruid.",
             desc="Edit a subscriber: name, note, e-mail, phone and login. Changing the tariff (gid), the group, the password or the blocked flag through this tool needs a further scope and a reason."),
    Endpoint("mb_kick_user", "userKickOnline", "/json/users/userkickfl", "write", note="uid.",
             desc="End a subscriber's current internet session. The subscriber can reconnect."),
    Endpoint("mb_create_sticky_note", "createStickyNotesUser", "/extjs/users/createstickynotesuser", "write",
             note="Use uid (not useruid).",
             desc="Add a sticky note to a subscriber."),
    Endpoint("mb_add_user_device", "addUserDev", "/extjs/users/adduserdev", "write",
             desc="Assign a device to a subscriber."),
    Endpoint("mb_create_ticket", None, "/extjs/ticketedit/setticket2x", "write",
             note="categoryid and prioritytypeid required. The creator is taken from the session. Link a user with "
                  "useruid (the address comes from the user).",
             desc="Create a ticket. The creator is the server's Mikbill account."),
    Endpoint("mb_update_ticket", None, "/extjs/ticketedit/setticket2x", "write",
             note="Same endpoint as create with ticketid; only the fields you send change.",
             desc="Edit a ticket. Only the fields you send change."),
    Endpoint("mb_add_ticket_note", None, "/extjs/ticketedit/addnote", "write",
             note="ticketid, note. The author is taken from the session.",
             desc="Add a note to a ticket. The author is the server's Mikbill account."),
    Endpoint("mb_set_ticket_status", None, "/extjs/ticketedit/putticketin", "write",
             note="status is open, in_work, performed or closed; ticket_ids lists the tickets; rating 1-5 only "
                  "with closed. Uses the acting staff id of the session.",
             desc="Set the status of one or more tickets: open, in_work, performed or closed. The acting staff member is the server's Mikbill account. The result shows the tickets afterwards."),
    Endpoint("mb_delete_sticky_note", None, "/extjs/users/deletestickynotesuser", "write",
             note="id.",
             desc="Delete one sticky note."),
    Endpoint("mb_delete_user_device", None, "/extjs/users/deleteuserdev", "write",
             note="uid (sent as useruid) and devid from mb_get_user_devices.",
             desc="Remove a device from a subscriber."),
    Endpoint("mb_delete_tickets", None, "/extjs/ticketedit/deletetickets", "write", gated=True,
             note="ticket_ids; sent as ticketid0, ticketid1, ... Deletes the tickets; the result shows their state afterwards.",
             desc="Delete tickets permanently. The preview and the result show each ticket's state."),
    Endpoint("mb_delete_ticket_note", None, "/extjs/ticketedit/deletenotes", "write",
             note="noteid.",
             desc="Delete one ticket note permanently."),
]

# ---- account (12, gated) -----------------------------------------------------------

ACCOUNT: list[Endpoint] = [
    Endpoint("mb_create_user", "createAbonent", "/extjs/users/createabonent", "account", gated=True,
             note="Needs a sector with free IPs.",
             desc="Create a subscriber on a tariff, in a sector and at an address. Returns the new useruid."),
    Endpoint("mb_freeze_user", "setFreezeUser", "/json/users/dofreezeuserfl", "account", gated=True,
             desc="Freeze a subscriber's account, now or for a period."),
    Endpoint("mb_bulk_enable_users", None, "/json/users/enableusersmulti", "account", gated=True,
             note="set_users_list.",
             desc="Enable the listed subscriber accounts."),
    Endpoint("mb_bulk_disable_users", None, "/json/users/disableusersmulti", "account", gated=True,
             note="set_users_list.",
             desc="Disable the listed subscriber accounts."),
    Endpoint("mb_bulk_unfreeze_users", None, "/json/users/unfreezeusersmulti", "account", gated=True,
             note="set_users_list.",
             desc="Unfreeze the listed subscriber accounts."),
    Endpoint("mb_bulk_enable_internet", None, "/json/users/enableinternetmulti", "account", gated=True,
             note="set_users_list.",
             desc="Turn internet access on for the listed subscribers."),
    Endpoint("mb_disconnect_user", None, "/json/users/otkluserflex", "account", gated=True,
             note="Sets state 3 (disconnected). Takes uid and cena_freeze.",
             desc="Disconnect a subscriber (account state 3)."),
    Endpoint("mb_bulk_delete_users", None, "/json/users/deleteusersmulti", "account", gated=True,
             note="SOFT delete. set_users_list.",
             desc="Soft-delete the listed subscribers."),
    Endpoint("mb_delete_user", None, "/json/users/deluserflex", "account", gated=True,
             note="SOFT delete: state 4, deletedtable 1. Row remains.",
             desc="Soft-delete a subscriber."),
    Endpoint("mb_delete_disconnected_user", None, "/json/users/deluserotklflex", "account", gated=True,
             note="SOFT delete of a disconnected user. uid, gid, itog_summa, type_list=2.",
             desc="Soft-delete a subscriber who is disconnected (state 3)."),
    Endpoint("mb_delete_user_full", None, "/json/users/delfulluserflex", "account", gated=True,
             status="denied", note="'Denied by settings' on the demo. Maps to denied_by_settings.",
             desc="Delete a subscriber permanently, with the whole record."),
    Endpoint("mb_restore_user", None, "/json/users/enableusersmulti", "account", gated=True,
             note="Restores a soft-deleted user (state 4). Without gid it uses enableusersmulti and the user keeps "
                  "the old tariff. With gid it uses vkluserflex and also puts the user on that tariff, which can "
                  "charge the tariff's first fee. Only for deleted users.",
             desc="Restore a soft-deleted subscriber. Without gid the user keeps the old tariff."),
]

# ---- finance (15, gated) -----------------------------------------------------------------

FINANCE: list[Endpoint] = [
    Endpoint("mb_receive_payment", None, "/json/users/paymentflex", "finance", gated=True,
             note="The admin UI's Payment window: an ordinary top-up (history type 'Пополнение счета'). uid, deposit, "
                  "prim2 plus beznal_payment, beznal_date, do_fiscalization. The answer is a receipt: bughid (payment "
                  "id), bughtime, fio, address, and deposit and credit as they were BEFORE the payment. Mikbill ignores a "
                  "zero or negative deposit (success, nothing happens) and does not require prim2.",
             desc="Register a payment received from a subscriber (an ordinary top-up) and add it to the balance. Moves "
                  "money. beznal_payment marks it as non-cash; do_fiscalization asks Mikbill to send a fiscal receipt "
                  "when a cash register is configured. For other balance operations (promotions, recalculations, "
                  "fees) use mb_create_payment."),
    Endpoint("mb_create_payment", "payment", "/json/users/paymentdoflex", "finance", gated=True,
             note="Moves money. uid, bughtypeid, deposit, prim2. The UI uses this endpoint for the promotion, "
                  "recalculation, subscription-fee and connect/repair operation forms, each with its own list of "
                  "bughtypeid values.",
             desc="Add a balance operation of a given type to one subscriber: a promotion, a recalculation, a fee. "
                  "bughtypeid comes from mb_list_payment_types. Fee types (a penalty of 1 lowered the "
                  "balance by 1) take money off the balance, promotion types add it. Moves money. For an ordinary payment received from the subscriber use "
                  "mb_receive_payment."),
    Endpoint("mb_bulk_payment", None, "/json/users/paymentdomulti", "finance", gated=True,
             note="Moves money on every listed account. set_users_list, deposit, prim2, bughtypeid.",
             desc="Add the same payment to every listed subscriber's balance. Moves money."),
    Endpoint("mb_change_tariff", "doChangeTarifFlex", "/json/users/dochangetarifflex", "finance", gated=True,
             note="uid, gid.",
             desc="Schedule a tariff change for a subscriber."),
    Endpoint("mb_enable_flex_tariff", "vklUserFlex", "/json/users/vkluserflex", "finance", gated=True,
             note="Reactivates a disconnected user (state 3). Needs uid, gid, itog_summa, type_list=2.",
             desc="Reactivate a disconnected subscriber (state 3) on a tariff. Can charge the tariff's fee."),
    Endpoint("mb_add_packet", "addPacket", "/json/tarif/addtarifflex", "finance", gated=True,
             note="tarif (name).",
             desc="Create a tariff (packet) with the given name."),
    Endpoint("mb_edit_packet", "editPacket", "/json/tarif/updatetariffl", "finance", gated=True,
             note="gid, packet, alias. Other form fields exist.",
             desc="Rename a tariff or change its alias."),
    Endpoint("mb_delete_packet", None, "/json/tarif/deltarifflex", "finance", gated=True,
             note="Check first with delchecktarifflex.",
             desc="Delete a tariff. Refused while subscribers use it, unless force is true."),
    Endpoint("mb_set_personal_service", "setPersonalServiceUser", "/json/users/setpersonalserviceuser",
             "finance", gated=True,
             status="blocked",
             note="Service must have a configured portal. serviceid 101 (portal 0) works. serviceid 115 (portal 18, Wink) fails with error 27.",
             desc="Turn a personal service on for a subscriber. Can add a recurring charge."),
    Endpoint("mb_unset_personal_service", None, "/json/users/unsetpersonalserviceuser",
             "finance", gated=True, note="uid, serviceid (activate, code optional).",
             desc="Turn a personal service off for a subscriber."),
    Endpoint("mb_set_credit", None, "/extjs/users/credit", "finance", gated=True,
             note="The admin UI's change-credit window. uid, credit. Replaces the credit (10 then 25 leaves 25), it does "
                  "not add. Above the installation's maximum Mikbill answers error 2 'credit summa greater than "
                  "allowed' (the text is in error_text). A zero or negative credit is ignored with success; use "
                  "mb_remove_credit_single to clear. /extjs/users/creditsumma?uid= shows the limits (credit_maximum).",
             desc="Set one subscriber's credit limit to the given amount. It replaces the current credit instead of "
                  "adding to it, and Mikbill refuses amounts above its maximum. Moves money."),
    Endpoint("mb_remove_credit_single", None, "/json/users/creditnullfl", "finance", gated=True, note="uid.",
             desc="Remove one subscriber's credit."),
    Endpoint("mb_bulk_remove_credit", None, "/json/users/creditnullmulti", "finance", gated=True,
             note="set_users_list.",
             desc="Remove the credit of every listed subscriber."),
    Endpoint("mb_bulk_issue_credit", None, "/json/users/creditmulti", "finance", gated=True,
             note="set_users_list, credit.",
             desc="Give the same credit to every listed subscriber."),
    Endpoint("mb_use_voucher", None, "/json/users/usevaucherfl", "finance", gated=True,
             note="uid, code. Only the wrong-code path was exercised. A valid code adds balance.",
             desc="Redeem a voucher code for a subscriber."),
]

# ---- admin (33): system options (read-only), and, gated, user groups, address and infrastructure objects ----

ADMIN: list[Endpoint] = [
    # system options: a read (it changes nothing, so no preview and readOnlyHint), but the data holds passwords, tokens
    # and keys, so it is also gated: the admin scope and a reason, which is logged.
    Endpoint("mb_get_system_options", "getSystemOptions", "/extjs/users/getsystemoptions", "admin", gated=True,
             read_only=True,
             note="Server paths and settings, with passwords, tokens and keys that the server masks in the answer. "
                  "Gated although read-only: a reason is required and logged.",
             desc="Get Mikbill's system options (server settings and paths). Values of password, secret, key and token options are masked."),
    # user groups
    Endpoint("mb_create_user_group", "createUsersGroup", "/json/options/setusersgroup", "admin", gated=True,
             note="usersgroupname, share, description.",
             desc="Create a user group."),
    Endpoint("mb_update_user_group", None, "/json/options/setusersgroup", "admin", gated=True,
             note="Same URI as create. With usersgroupid it updates. Verified.",
             desc="Rename a user group or change its description."),
    Endpoint("mb_delete_user_group", None, "/json/options/deleteusersgroup", "admin", gated=True,
             note="Use delete_list (the UI param). usersgroupid always returns 'in use'. Dry-run must count users in the group.",
             desc="Delete a user group."),
    # address objects: settlements, neighborhoods, lanes, houses
    Endpoint("mb_add_settlement", "addSettlement", "/json/options/addsettlement", "admin", gated=True,
             desc="Create a settlement."),
    Endpoint("mb_edit_settlement", "editSettlement", "/json/options/editsettlement", "admin", gated=True,
             desc="Rename a settlement."),
    Endpoint("mb_delete_settlement", None, "/json/options/deletesettlement", "admin", gated=True,
             note="Check first with checkdeletesettlement. Mikbill does not enforce the check.",
             desc="Delete a settlement. Refused while neighborhoods, lanes or houses belong to it, unless force is true."),
    Endpoint("mb_add_neighborhood", "addNeighborhood", "/json/options/addneighborhood", "admin", gated=True,
             desc="Create a neighborhood in a settlement."),
    Endpoint("mb_edit_neighborhood", "editNeighborhood", "/json/options/editneighborhood", "admin", gated=True,
             desc="Rename a neighborhood or move it to another settlement."),
    Endpoint("mb_delete_neighborhood", None, "/json/options/deleteneighborhood", "admin", gated=True,
             note="Mikbill does not enforce its dependency check; the dry-run shows it.",
             desc="Delete a neighborhood. Refused while houses belong to it, unless force is true."),
    Endpoint("mb_add_lane", "addLane", "/json/options/addlanesflex", "admin", gated=True,
             desc="Create a lane (street) in a settlement."),
    Endpoint("mb_edit_lane", "editLane", "/json/options/editlaneflex", "admin", gated=True,
             desc="Rename a lane or move it to another settlement."),
    Endpoint("mb_delete_lane", None, "/json/options/dellaneflex", "admin", gated=True,
             note="Mikbill does not enforce its dependency check; the dry-run shows it.",
             desc="Delete a lane. Refused while houses belong to it, unless force is true."),
    Endpoint("mb_add_house", "addHouse", "/json/options/addhouseflex", "admin", gated=True,
             desc="Create a house on a lane."),
    Endpoint("mb_edit_house", "editHouse", "/json/options/edithouselaneflex", "admin", gated=True,
             desc="Edit a house."),
    Endpoint("mb_delete_house", "deleteHouse", "/json/options/delhouseflex", "admin", gated=True,
             status="blocked",
             note="Send houseid only. Error 8 means users still reference the house. delete_list reports success but deletes nothing.",
             desc="Delete a house. Mikbill refuses while subscribers, deleted ones included, are registered in it."),
    # sectors
    Endpoint("mb_add_sector", "addSector", "/json/options/sectoraddflex", "admin", gated=True,
             desc="Create a sector with the given name. IP settings are made in the admin UI."),
    Endpoint("mb_edit_sector", "editSector", "/json/options/sectoreditflex", "admin", gated=True,
             desc="Rename a sector."),
    Endpoint("mb_delete_sector", None, "/json/options/sectordelflex", "admin", gated=True,
             desc="Delete a sector."),
    # switch types and switches
    Endpoint("mb_add_switch_type", "addSwitchType", "/json/options/addswitchtypeflex", "admin", gated=True,
             note="Upstream wrapper calls it without params. With params it works.",
             desc="Create a switch type."),
    Endpoint("mb_edit_switch_type", "editSwitchType", "/json/options/editswithchtypeflex", "admin", gated=True,
             note="Upstream URI spelling kept.",
             desc="Edit a switch type."),
    Endpoint("mb_delete_switch_type", "delSwitchType", "/json/options/delswitchtypeflex", "admin", gated=True,
             desc="Delete a switch type."),
    Endpoint("mb_add_switch", "addSwitch", "/json/options/addswitchflex", "admin", gated=True,
             note="Upstream wrapper calls it without params. With params it works.",
             desc="Create a switch."),
    Endpoint("mb_edit_switch", "editSwitch", "/json/options/editswitchflex", "admin", gated=True,
             desc="Edit a switch."),
    Endpoint("mb_delete_switch", "delSwitch", "/json/options/delswitchflex", "admin", gated=True,
             desc="Delete a switch."),
    # device types
    Endpoint("mb_add_dev_type", "addDevType", "/json/users/adddevtype", "admin", gated=True,
             note="Requires devtypename. Calling with no params still returns success.",
             desc="Create a device type."),
    Endpoint("mb_delete_dev_type", None, "/json/users/deletedevtype", "admin", gated=True,
             note="devtypeid, sent as delete_list. With a plain devtypeid Mikbill answers success and deletes "
                  "nothing. A type still used by a device is refused.",
             desc="Delete a device type. Mikbill refuses while a device uses it."),
    # IP pools, real IPs, NAS
    Endpoint("mb_add_ip_pool", None, "/json/options/addippoolsflex", "admin", gated=True,
             note="poolname. Verified (created MCPTEST_POOL).",
             desc="Create an IP pool."),
    Endpoint("mb_delete_ip_pool", None, "/json/options/delippoolsflex", "admin", gated=True,
             note="poolname.",
             desc="Delete an IP pool."),
    Endpoint("mb_add_real_ip_block", None, "/json/options/addrealipflex", "admin", gated=True,
             note="localip, netmask, cidrmask, hostmin, hostmax, hosts. Verified (created 254 addresses).",
             desc="Add a block of real (public) IP addresses."),
    Endpoint("mb_delete_real_ip", None, "/json/options/delrealipflex", "admin", gated=True,
             note="One address per call (real).",
             desc="Delete one real IP address."),
    Endpoint("mb_add_nas", None, "/json/options/nasaddflex", "admin", gated=True,
             note="nasname, nasip, secret, shortname. Verified (created MCPTEST_NAS).",
             desc="Register a NAS (RADIUS client)."),
    Endpoint("mb_delete_nas", None, "/json/options/nasserverdelflex", "admin", gated=True,
             note="nasid.",
             desc="Delete a NAS."),
]

ALL: list[Endpoint] = READ + WRITE + ACCOUNT + FINANCE + ADMIN

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


# ---- MCP tool annotations ----------------------------------------------------------------------
# Hints for clients (readOnlyHint, destructiveHint, idempotentHint, openWorldHint) so that a harness can decide when to
# ask the user. They are hints, not a safety boundary: the real controls are the scopes, `reason` and the audit log.

_ADDITIVE_PREFIXES = ("mb_create_", "mb_add_")
_NOT_ADDITIVE = {"mb_create_payment"}                 # moves money, so it is treated as destructive
_IDEMPOTENT_WORDS = {"delete", "edit", "update", "set", "unset"}


def tool_annotations(ep: Endpoint) -> dict[str, bool]:
    """MCP ToolAnnotations fields for a tool, in snake_case."""
    if ep.is_read:
        return {"read_only_hint": True, "open_world_hint": False}
    additive = ep.tool.startswith(_ADDITIVE_PREFIXES) and ep.tool not in _NOT_ADDITIVE
    return {
        "read_only_hint": False,
        "destructive_hint": not additive,
        "idempotent_hint": bool(_IDEMPOTENT_WORDS & set(ep.tool.split("_"))),
        "open_world_hint": False,
    }


def tool_title(ep: Endpoint) -> str:
    return ep.tool.removeprefix("mb_").replace("_", " ").capitalize()
