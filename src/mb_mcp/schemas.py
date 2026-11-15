"""Per-tool request schemas, taken from fixtures/ (verified on the demo).

Field names are Mikbill's own. `defaults` are filled when the caller omits a field, `fixed` are
always sent, `rename` maps a tool argument to the Mikbill parameter name, and `uids` tools take
an array that is sent as `set_users_list=<uid,uid,...>`.

Field types are decided by the field name, in one place (ID_FIELDS, COUNT_FIELDS, MONEY_FIELDS,
PAYMENT_FIELDS, FLAG_FIELDS), because Mikbill reuses the same names across its forms. Mikbill parses
values leniently ("12abc" acts on 12), so everything that names a record or moves money is checked here
before it is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCALAR_TYPES = ["string", "integer", "number", "boolean"]

# ---- field types ---------------------------------------------------------------------------------

# Record ids: whole numbers, 0 or more ("0" means "none" on several Mikbill forms).
ID_FIELDS = frozenset({
    "uid", "useruid", "gid", "sectorid", "settlementid", "neighborhoodid", "laneid", "houseid", "swid", "swtypeid",
    "devtypeid", "devicetypeid", "devid", "usersgroupid", "nasid", "ticketid", "noteid", "categoryid",
    "prioritytypeid", "statustypeid", "serviceid", "bughtypeid", "id", "parent_swid", "dev_sectorid", "app_count_id",
})
# Ids that name the record a call acts on: 1 or more.
TARGET_ID_FIELDS = frozenset({"uid", "useruid"})
# Counts and other whole numbers, 0 or more.
COUNT_FIELDS = frozenset({
    "numports", "porches", "floors", "count_free_freeze", "count_free_freeze_use", "license_count",
    "installments_count", "hosts", "cidrmask", "page", "start", "mgmt_vlan", "user_vlan",
})
# Money that is credited to an account: more than 0 and capped by MB_MAX_PAYMENT (per account) and
# MB_MAX_PAYMENT_TOTAL (per bulk call).
PAYMENT_FIELDS = frozenset({"deposit", "credit"})
# Other amounts (prices and fees): plain decimals, 0 or more.
MONEY_FIELDS = frozenset({
    "itog_summa", "cena_freeze", "cena_sutok_freeze", "cena_unfreeze", "cost", "rent", "default_cost",
    "default_rent", "installments_price",
})
# 0/1 switches.
FLAG_FIELDS = frozenset({
    "use_installments", "lane_create", "house_create", "blocked", "do_freeze_now", "do_return_abonplata",
    "freeze_do_ever", "dont_use_uplink", "do_check_switch_port", "do_mac_autoreg_by_radius", "use_circut_template",
    "use_unit_find", "use_module_find", "do_ip_unnumbered", "strict_dhcp_mode", "show_ticket_to_user",
    "beznal_payment", "do_fiscalization",
})
# Calendar dates, YYYY-MM-DD.
DATE_FIELDS = frozenset({"beznal_date"})
MAX_TEXT_INPUT = 4000   # longest text argument accepted

# Arguments whose values may be written to the audit log as they are: ids, amounts, switches, enums and dates.
# Every other argument is free text (names, notes, addresses, logins, codes) and is masked there.
AUDIT_CLEAR_KEYS = (ID_FIELDS | COUNT_FIELDS | PAYMENT_FIELDS | MONEY_FIELDS | FLAG_FIELDS | DATE_FIELDS | frozenset({
    "uids", "ticket_ids", "user_labels", "states", "internet", "match", "limit", "detail", "status", "rating",
    "dogovor", "force", "type_list", "from_date", "to_date", "freeze_date_start", "freeze_date_stop",
    "performafter", "performbefore",
}))


@dataclass(frozen=True)
class ToolSchema:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    fixed: dict[str, Any] = field(default_factory=dict)
    rename: dict[str, str] = field(default_factory=dict)
    uids: bool = False          # tool takes `uids` (array of integers), sent as set_users_list
    allow_empty: bool = False   # an empty {} body from Mikbill means "no rows"
    ticket_ids: str | None = None   # tool takes `ticket_ids`: "set_list" (set_list=1,2) or "enumerate" (ticketid0=1, ...)
    enums: dict[str, tuple[Any, ...]] = field(default_factory=dict)  # allowed values per argument
    row_filters: tuple[str, ...] = ()   # optional arguments applied to the returned rows, never sent to Mikbill
    no_success_key: bool = False        # the response has `data` but no `success` field
    integers: tuple[str, ...] = ()      # further arguments that must be whole numbers (a string of digits is accepted)
    help: dict[str, str] = field(default_factory=dict)   # per-tool argument descriptions; FIELD_HELP is the fallback
    user_search: bool = False           # the Mikbill user search form: adds states, user_labels, internet and match
    row_limit: int | None = None        # default of the `limit` argument that caps returned rows; None: no such argument
    # Arguments that need a further scope on top of the tool's own, e.g. a tariff change through a plain edit.
    # A call that sends one of them also needs a `reason`.
    field_scopes: dict[str, str] = field(default_factory=dict)
    # Row fields returned by default. When set, the tool takes `detail` ("summary" or "full").
    summary: tuple[str, ...] = ()

    @property
    def fields(self) -> tuple[str, ...]:
        return self.required + self.optional


# The user search form (searchflex). Values verified on the demo (fixtures/search_and_online_verified.json).
SEARCH_STATES = {"normal": "search_normal_state", "disabled": "search_otkluchen_state",
                 "frozen": "search_frozen_state", "deleted": "search_deleted_state"}
SEARCH_INTERNET = {"on": 1, "off": 2}          # search_internet: 0 (omitted) is all subscribers
SEARCH_MATCH = {"all": "1", "any": "0"}        # op: 1 = every filter must match, 0 = any filter
SEARCH_CONTRACT_STATES = (0, 1, 2, 3)          # dogovor: no data, active, suspended, closed
MAX_ROWS = 1000
DEFAULT_LIST_ROWS = 500   # default `limit` of the dictionary lists and the long histories
# Where the admin UI lists the operation types (bughtypeid) for its operation forms: one list per form.
PAYMENT_TYPE_KINDS = ("fee", "promotion", "correction", "connection")
DETAIL = ("summary", "full")

SEARCH_HELP = {
    "uid": "Exact subscriber uid. Mikbill ignores every other filter when uid is given, so it cannot be combined.",
    "user": "Text in the login, name, note or company name.",
    "address": "Text in the address, for example a street name.",
    "phone": "Text in a phone number or e-mail address.",
    "local_ip": "Text in the IP address, MAC address or device serial.",
    "sticky_note": "Text in the subscriber's sticky notes.",
    "app": "Apartment.",
    "settlementid": "Settlement id (see mb_list_settlements).",
    "neighborhoodid": "Neighborhood id (see mb_list_neighborhoods).",
    "laneid": "Lane id (see mb_list_lanes).",
    "houseid": "House id (see mb_list_houses).",
    "gid": "Tariff (packet) id (see mb_list_packets).",
    "swid": "Switch id (see mb_list_switches).",
    "sectorid": "Sector id (see mb_list_sectors).",
    "switchport": "Switch port.",
    "dogovor": "Contract state: 0 no data, 1 active, 2 suspended, 3 closed.",
    "pon_central_closure_txt": "Text in the PON central closure.",
    "pon_connection_box_txt": "Text in the PON connection box.",
}

# Rows of mb_search_users and mb_list_online_users are full subscriber records. By default only these fields are
# returned; `detail: "full"` returns the whole row.
SEARCH_SUMMARY = ("useruid", "user", "fio", "state", "status", "gid", "packet", "deposit", "credit", "blocked",
                  "online", "address", "app", "numdogovor", "days_left")
ONLINE_SUMMARY = ("useruid", "user", "fio", "gid", "packet", "sectorid", "framed_ip", "ip", "fstart_time", "time_on",
                  "in_bytes", "out_bytes", "swid", "switchport", "swichname", "address", "app", "state", "status")

# Descriptions of arguments, by Mikbill field name. A tool's own `help` wins over these.
FIELD_HELP: dict[str, str] = {
    # ids
    "uid": "Subscriber uid.",
    "useruid": "Subscriber uid.",
    "gid": "Tariff (packet) id, see mb_list_packets.",
    "sectorid": "Sector id, see mb_list_sectors.",
    "settlementid": "Settlement id, see mb_list_settlements.",
    "neighborhoodid": "Neighborhood id, see mb_list_neighborhoods.",
    "laneid": "Lane (street) id, see mb_list_lanes.",
    "houseid": "House id, see mb_list_houses.",
    "swid": "Switch id, see mb_list_switches.",
    "swtypeid": "Switch type id, see mb_list_switch_types.",
    "devtypeid": "Device type id, see mb_list_dev_types.",
    "devicetypeid": "Device class id of the switch type, as on the admin UI's switch type form.",
    "devid": "Device id, see mb_get_user_devices.",
    "usersgroupid": "User group id, see mb_list_user_groups.",
    "nasid": "NAS id.",
    "ticketid": "Ticket id, see mb_list_tickets.",
    "noteid": "Ticket note id, see mb_get_ticket_notes.",
    "categoryid": "Ticket category id, see mb_list_ticket_categories.",
    "prioritytypeid": "Ticket priority id, see mb_list_ticket_priorities.",
    "statustypeid": "Ticket status: 1 open, 2 closed, 3 in work, 4 performed.",
    "serviceid": "Personal service id.",
    "bughtypeid": "Payment (operation) type id. The ids are specific to the installation: take one from "
                  "mb_list_payment_types. Mikbill accepts an unknown id and records a payment with no type, so the "
                  "server refuses ids that are not in the list.",
    "id": "Sticky note id.",
    "parent_swid": "Id of the uplink switch, 0 for none.",
    "dev_sectorid": "Sector id of the device.",
    "app_count_id": "Apartment count of the house, as on the admin UI's house form.",
    # subscriber
    "user": "Login.",
    "password": "Account password.",
    "fio": "Full name.",
    "phone": "Phone number.",
    "email": "E-mail address.",
    "prim": "Note on the subscriber.",
    "app": "Apartment.",
    "blocked": "1 blocks the subscriber, 0 unblocks.",
    "use_installments": "Installments switch of the create form: 1 on, 0 off.",
    "lane_name": "Name of a new lane; used with lane_create 1.",
    "lane_create": "1 creates the lane named in lane_name, 0 uses laneid.",
    "house_name": "Name of a new house; used with house_create 1.",
    "house_create": "1 creates the house named in house_name, 0 uses houseid.",
    "from_date": "Start of the period (YYYY-MM-DD).",
    "to_date": "End of the period (YYYY-MM-DD).",
    "description": "Description text.",
    # devices
    "dev_mac": "Device MAC address.",
    "device_sn": "Device serial number.",
    "dev_ip": "Device IP address.",
    "cost": "Device price.",
    "rent": "Device rent fee.",
    "owner": "Owner field of the device form (usually 0).",
    "partner": "Partner field of the device form (usually 0).",
    "card_id": "Smart card id of a TV device.",
    "module_id": "Module id of a TV device.",
    "portal_login": "Login on the service portal.",
    "portal_password": "Password on the service portal.",
    "activation_code": "Activation code of the device.",
    "license_count": "Number of licenses.",
    "installments_price": "Installment price.",
    "installments_count": "Number of installments.",
    "devtypename": "Device type name.",
    "type": "Type field of the device type form (usually 0).",
    "default_cost": "Default price for devices of this type.",
    "default_rent": "Default rent fee for devices of this type.",
    "default_owner": "Default owner field for devices of this type (usually 0).",
    # address objects
    "settlementname": "Settlement name.",
    "neighborhoodname": "Neighborhood name.",
    "lane": "Lane (street) name.",
    "house": "House number or name.",
    "porches": "Number of entrances.",
    "floors": "Number of floors.",
    "postal_index": "Postal code.",
    "sector": "Sector name.",
    # switches
    "nameswitch": "Switch name.",
    "ip": "Management IP address.",
    "mac": "MAC address.",
    "address": "Where the switch is installed.",
    "mgmt_vlan": "Management VLAN id.",
    "user_vlan": "User VLAN id.",
    "swtypename": "Switch type name.",
    "numports": "Number of ports.",
    "snmp_type": "SNMP type field of the switch type form (usually 0).",
    "montype": "Monitoring type field of the switch type form (usually 0).",
    "cisco": "Cisco field of the switch type form (usually 0).",
    "dont_use_uplink": "Switch type option 'do not use uplink': 1 on, 0 off.",
    "do_check_switch_port": "Switch type option 'check switch port': 1 on, 0 off.",
    "do_mac_autoreg_by_radius": "Switch type option 'MAC auto-registration by RADIUS': 1 on, 0 off.",
    "use_circut_template": "Switch type option 'use circuit template': 1 on, 0 off.",
    "use_unit_find": "Switch type option 'find unit': 1 on, 0 off.",
    "use_module_find": "Switch type option 'find module': 1 on, 0 off.",
    "do_ip_unnumbered": "Switch type option 'IP unnumbered': 1 on, 0 off.",
    "strict_dhcp_mode": "Switch type option 'strict DHCP mode': 1 on, 0 off.",
    # freeze, disconnect, reactivate
    "do_freeze_now": "1 freezes the account now, 0 does not.",
    "do_return_abonplata": "1 returns the subscription fee, 0 does not.",
    "freeze_do_ever": "1 freezes with no end date, 0 uses freeze_date_stop.",
    "freeze_date_start": "First day of the freeze (YYYY-MM-DD).",
    "freeze_date_stop": "Last day of the freeze (YYYY-MM-DD).",
    "cena_freeze": "Fee charged for the action, 0 for none.",
    "cena_sutok_freeze": "Fee per day of freeze, 0 for none.",
    "count_free_freeze": "Number of free freezes the subscriber may use.",
    "count_free_freeze_use": "Number of free freezes already used.",
    "cena_unfreeze": "Fee charged for unfreezing, 0 for none.",
    "itog_summa": "Total amount Mikbill charges for the action, 0 for none.",
    # finance
    "deposit": "Amount credited to each account. More than 0; the server caps it (MB_MAX_PAYMENT).",
    "credit": "Credit amount for each account. More than 0; the server caps it (MB_MAX_PAYMENT).",
    "prim2": "Comment stored with the payment.",
    "beznal_payment": "1 marks the payment as non-cash (a bank transfer), 0 or omitted for cash.",
    "beznal_date": "Date of a non-cash payment, YYYY-MM-DD. Only with beznal_payment 1.",
    "do_fiscalization": "1 asks Mikbill to send a fiscal receipt when a cash register integration is configured, 0 "
                        "does not. Omit to use Mikbill's default.",
    "tarif": "Name of the new tariff.",
    "packet": "Tariff name.",
    "alias": "Tariff alias.",
    "activate": "Activate field of the admin UI form (optional).",
    "code": "Code field of the admin UI form (optional).",
    # admin objects
    "usersgroupname": "Group name.",
    "share": "Share field of the group form (0 by default).",
    "poolname": "IP pool name.",
    "real": "The real IP address to delete.",
    "localip": "Network address of the block, for example 198.51.100.0.",
    "netmask": "Netmask, for example 255.255.255.252.",
    "cidrmask": "Prefix length, for example 30.",
    "hostmin": "First host address.",
    "hostmax": "Last host address.",
    "hosts": "Number of host addresses.",
    "nasname": "NAS name.",
    "nasip": "NAS IP address.",
    "secret": "RADIUS shared secret.",
    "shortname": "NAS short name.",
    # tickets
    "phones": "Contact phone numbers.",
    "emphasis": "Emphasis mark of the ticket: 1 on, 0 off.",
    "show_ticket_to_user": "1 shows the ticket in the subscriber's cabinet, 0 hides it.",
    "order_number": "Order number.",
    "performafter": "Perform not earlier than this date and time (YYYY-MM-DD HH:MM:SS).",
    "performbefore": "Perform not later than this date and time (YYYY-MM-DD HH:MM:SS).",
    "set_list": "Comma list of performer staff ids, see mb_list_ticket_performers.",
    "note": "Note text.",
    "status": "New status.",
    "rating": "Rating from 1 to 5; only with status closed.",
    # paging
    "page": "Page number.",
    "start": "Offset of the first row.",
    "limit": "Most rows to return.",
}


def _S(required: tuple[str, ...] = (), optional: tuple[str, ...] = (), **kw: Any) -> ToolSchema:
    return ToolSchema(required=required, optional=optional, **kw)


_FREEZE_OPT = ("do_return_abonplata", "freeze_do_ever", "freeze_date_start", "freeze_date_stop", "cena_freeze",
               "cena_sutok_freeze", "count_free_freeze", "count_free_freeze_use", "cena_unfreeze")

SCHEMAS: dict[str, ToolSchema] = {
    # ---- read ----
    "mb_check_session": _S(),
    "mb_get_payment_info": _S(("uid",)),
    # Four UI lists (fee, promotion, correction, connection); the runtime reads all of them, or the one in `kind`.
    "mb_list_payment_types": _S((), ("kind",), enums={"kind": PAYMENT_TYPE_KINDS},
                                help={"kind": "Only this group of types. Omit to list all four."}),
    "mb_count_users": _S(),
    # The same filters as the admin UI's search tab. At least one filter is required: an empty search returns every
    # subscriber (905 on the demo, deleted ones included), and Mikbill does not page the result.
    "mb_search_users": _S(
        (), tuple(SEARCH_HELP),
        integers=("uid", "settlementid", "neighborhoodid", "laneid", "houseid", "gid", "swid", "sectorid", "dogovor"),
        enums={"dogovor": SEARCH_CONTRACT_STATES}, help=SEARCH_HELP, user_search=True, row_limit=100,
        summary=SEARCH_SUMMARY),
    "mb_get_user": _S(("uid",)),
    "mb_get_user_payment_history": _S(("uid",), ("from_date", "to_date"), row_limit=DEFAULT_LIST_ROWS),
    "mb_get_user_short_history": _S(("uid",), row_limit=DEFAULT_LIST_ROWS),
    "mb_get_user_canvas_stat": _S(("uid",)),
    # Mikbill wants the parameter named useruid here; `uid` returns an empty {} even when the user has devices.
    "mb_get_user_devices": _S(("uid",), rename={"uid": "useruid"}, allow_empty=True),
    "mb_list_dev_types": _S(row_limit=DEFAULT_LIST_ROWS),
    "mb_list_settlements": _S(row_limit=DEFAULT_LIST_ROWS),
    "mb_list_neighborhoods": _S(row_limit=DEFAULT_LIST_ROWS),
    "mb_list_lanes": _S(row_limit=DEFAULT_LIST_ROWS),
    "mb_list_houses": _S(row_limit=DEFAULT_LIST_ROWS),
    "mb_list_sectors": _S(row_limit=DEFAULT_LIST_ROWS),
    "mb_list_switches": _S(row_limit=DEFAULT_LIST_ROWS),
    "mb_list_switch_types": _S(row_limit=DEFAULT_LIST_ROWS),
    # Mikbill ignores page, start and limit on this list (verified: always every tariff), so they are not offered;
    # `limit` and `offset` are the server's own.
    "mb_list_packets": _S(row_limit=DEFAULT_LIST_ROWS),
    "mb_list_user_groups": _S(row_limit=DEFAULT_LIST_ROWS),
    # Mikbill ignores every parameter for inetonlinefl (verified), so the filters are applied to the rows.
    "mb_list_online_users": _S(
        (), ("useruid", "user", "gid", "sectorid", "swid", "settlementid"),
        row_filters=("useruid", "user", "gid", "sectorid", "swid", "settlementid"),
        integers=("useruid", "gid", "sectorid", "swid", "settlementid"), row_limit=200, summary=ONLINE_SUMMARY,
        help={"useruid": "Only this subscriber uid.", "user": "Only this exact login.", "gid": "Only this tariff id.",
              "sectorid": "Only this sector id.", "swid": "Only this switch id.",
              "settlementid": "Only this settlement id."}),
    "mb_get_system_options": _S(),
    # ---- write ----
    "mb_create_user": _S(
        ("user", "password", "fio", "gid", "sectorid", "settlementid", "neighborhoodid", "laneid", "houseid", "app"),
        ("phone", "email", "prim", "use_installments", "lane_name", "lane_create", "house_name", "house_create"),
        defaults={"phone": "", "email": "", "prim": "", "use_installments": 0, "lane_name": "", "lane_create": 0,
                  "house_name": "", "house_create": 0},
        help={"user": "Login of the new subscriber.", "password": "Password of the new subscriber.",
              "sectorid": "Sector id, see mb_list_sectors. The sector must have free IP addresses."}),
    # A plain edit can also change the tariff, the group, the password and the blocked flag. Those four are not
    # "routine edits": each needs the scope that owns the same change elsewhere, and a reason.
    "mb_update_user": _S(("useruid",), ("fio", "prim", "email", "phone", "blocked", "user", "gid", "usersgroupid",
                                         "password"),
                         field_scopes={"gid": "finance", "usersgroupid": "account", "password": "account",
                                       "blocked": "account"},
                         help={"gid": "New tariff id, applied at once. Needs the finance scope and a reason.",
                               "usersgroupid": "New user group id. Needs the account scope and a reason.",
                               "password": "New account password. Needs the account scope and a reason.",
                               "blocked": "1 blocks the subscriber, 0 unblocks. Needs the account scope and a reason.",
                               "user": "New login."}),
    "mb_kick_user": _S(("uid",)),
    "mb_create_sticky_note": _S(("uid", "description"), ("prim",),
                                help={"description": "Text of the sticky note.", "prim": "Further note text."}),
    "mb_add_user_device": _S(
        ("useruid", "devtypeid"),
        ("dev_mac", "device_sn", "cost", "rent", "owner", "partner", "dev_ip", "dev_sectorid", "card_id", "module_id",
         "portal_login", "portal_password", "activation_code", "license_count", "installments_price",
         "installments_count")),
    "mb_add_dev_type": _S(("devtypename",), ("type", "default_cost", "default_rent", "default_owner", "description")),
    "mb_add_settlement": _S(("settlementname",)),
    "mb_edit_settlement": _S(("settlementid", "settlementname")),
    "mb_add_neighborhood": _S(("settlementid", "neighborhoodname")),
    "mb_edit_neighborhood": _S(("settlementid", "neighborhoodid", "neighborhoodname")),
    "mb_add_house": _S(("house", "laneid", "neighborhoodid"), ("porches", "floors", "app_count_id", "postal_index")),
    "mb_edit_house": _S(("houseid", "house", "laneid", "neighborhoodid"),
                        ("porches", "floors", "postal_index", "app_count_id")),
    "mb_add_lane": _S(("settlementid", "lane")),
    "mb_edit_lane": _S(("settlementid", "laneid", "lane")),
    "mb_add_sector": _S(("sector",)),
    "mb_edit_sector": _S(("sectorid", "sector")),
    "mb_edit_switch": _S(("swid", "nameswitch", "swtypeid"), ("ip", "mac", "address")),
    "mb_add_switch": _S(("nameswitch", "swtypeid"), ("ip", "mac", "address", "parent_swid", "mgmt_vlan", "user_vlan")),
    "mb_add_switch_type": _S(("swtypename", "devicetypeid", "numports"), ("snmp_type", "montype", "cisco")),
    "mb_edit_switch_type": _S(
        ("swtypeid", "swtypename", "devicetypeid", "numports"),
        ("snmp_type", "dont_use_uplink", "do_check_switch_port", "do_mac_autoreg_by_radius", "use_circut_template",
         "use_unit_find", "use_module_find", "do_ip_unnumbered", "strict_dhcp_mode", "montype", "cisco")),
    # ---- account ----
    "mb_freeze_user": _S(("uid", "do_freeze_now"), _FREEZE_OPT,
                         defaults={"do_return_abonplata": 0, "freeze_do_ever": 0, "cena_freeze": 0,
                                   "cena_sutok_freeze": 0, "count_free_freeze": 0, "count_free_freeze_use": 0,
                                   "cena_unfreeze": 0},
                         help={"cena_freeze": "Fee charged for the freeze, 0 for none."}),
    "mb_bulk_enable_users": _S(uids=True),
    "mb_bulk_disable_users": _S(uids=True),
    "mb_bulk_unfreeze_users": _S(uids=True),
    "mb_bulk_enable_internet": _S(uids=True),
    "mb_disconnect_user": _S(("uid",), ("cena_freeze",), defaults={"cena_freeze": 0},
                             help={"cena_freeze": "Fee charged for the disconnect, 0 for none."}),
    "mb_delete_user": _S(("uid",)),
    "mb_bulk_delete_users": _S(uids=True),
    "mb_delete_disconnected_user": _S(("uid", "gid"), ("itog_summa",), defaults={"itog_summa": 0},
                                      fixed={"type_list": 2},
                                      help={"gid": "The user's tariff id, as the admin UI sends it."}),
    "mb_delete_user_full": _S(("uid",)),
    "mb_delete_house": _S(("houseid",)),
    "mb_delete_switch": _S(("swid",)),
    "mb_delete_switch_type": _S(("swtypeid",)),
    "mb_delete_sticky_note": _S(("id",)),
    # devtypeid is a silent no-op (success, nothing deleted); the UI's delete_list really deletes and enforces "in use".
    "mb_delete_dev_type": _S(("devtypeid",), rename={"devtypeid": "delete_list"}),
    "mb_delete_settlement": _S(("settlementid",)),
    "mb_delete_neighborhood": _S(("neighborhoodid",)),
    "mb_delete_lane": _S(("laneid",)),
    "mb_delete_sector": _S(("sectorid",)),
    "mb_delete_ip_pool": _S(("poolname",)),
    "mb_delete_real_ip": _S(("real",)),
    "mb_delete_nas": _S(("nasid",)),
    "mb_delete_user_device": _S(("uid", "devid"), rename={"uid": "useruid"}),
    # Restore: runtime picks enableusersmulti (no gid) or vkluserflex (with gid), see Runtime._restore_plan.
    # With gid it assigns a tariff and can charge its first fee, which is a finance action.
    "mb_restore_user": _S(("uid",), ("gid", "itog_summa"), field_scopes={"gid": "finance"},
                          help={"gid": "Tariff id to put the user on. Can charge the tariff's first fee. Needs the "
                                       "finance scope as well. Omit to keep the old tariff.",
                                "itog_summa": "Total amount Mikbill charges; only together with gid."}),
    # ---- finance ----
    "mb_create_payment": _S(("uid", "bughtypeid", "deposit", "prim2")),
    # The admin UI's Payment window (paymentflex): an ordinary top-up. Zero and negative amounts are silently ignored
    # by Mikbill (success, nothing happens); they are refused here by the PAYMENT_FIELDS rule.
    "mb_receive_payment": _S(("uid", "deposit", "prim2"), ("beznal_payment", "beznal_date", "do_fiscalization")),
    # /extjs/users/credit replaces the credit limit with the amount (10 then 25 leaves 25); above Mikbill's maximum it
    # answers error 2, and 0 or a negative amount is silently ignored (refused here).
    "mb_set_credit": _S(("uid", "credit")),
    "mb_bulk_payment": _S(("bughtypeid", "deposit", "prim2"), uids=True),
    "mb_change_tariff": _S(("uid", "gid"), help={"gid": "New tariff id, see mb_list_packets."}),
    "mb_enable_flex_tariff": _S(("uid", "gid"), ("itog_summa",), defaults={"itog_summa": 0}, fixed={"type_list": 2},
                                help={"gid": "Tariff id to put the user on, see mb_list_packets."}),
    "mb_add_packet": _S(("tarif",)),
    "mb_edit_packet": _S(("gid", "packet"), ("alias",)),
    "mb_delete_packet": _S(("gid",)),
    "mb_set_personal_service": _S(("uid", "serviceid")),
    "mb_unset_personal_service": _S(("uid", "serviceid"), ("activate", "code")),
    "mb_remove_credit_single": _S(("uid",)),
    "mb_bulk_remove_credit": _S(uids=True),
    "mb_bulk_issue_credit": _S(("credit",), uids=True),
    "mb_use_voucher": _S(("uid", "code"), help={"code": "Voucher code."}),
    # ---- admin ----
    "mb_create_user_group": _S(("usersgroupname",), ("share", "description"), defaults={"share": 0, "description": ""}),
    "mb_update_user_group": _S(("usersgroupid", "usersgroupname"), ("share", "description"),
                               defaults={"share": 0, "description": ""}),
    # The UI sends delete_list. `usersgroupid` always returns "in use" (SPEC.md, "Delete parameters").
    "mb_delete_user_group": _S(("usersgroupid",), rename={"usersgroupid": "delete_list"}),
    "mb_add_ip_pool": _S(("poolname",)),
    "mb_add_real_ip_block": _S(("localip", "netmask", "cidrmask", "hostmin", "hostmax", "hosts")),
    "mb_add_nas": _S(("nasname", "nasip"), ("secret", "shortname")),
    # ---- tickets ----
    # getticketslist2x has no `success` field and ignores filter parameters, so the filters are applied to the rows.
    # Every row embeds its notes, which are free text from staff and subscribers, so the rows are capped.
    "mb_list_tickets": _S((), ("statustypeid", "categoryid", "prioritytypeid", "ticketid"),
                          row_filters=("statustypeid", "categoryid", "prioritytypeid", "ticketid"),
                          no_success_key=True, row_limit=50,
                          help={"statustypeid": "Only tickets in this status: 1 open, 2 closed, 3 in work, 4 performed.",
                                "categoryid": "Only this category id.", "prioritytypeid": "Only this priority id.",
                                "ticketid": "Only this ticket id."}),
    "mb_get_ticket_notes": _S(("ticketid",)),
    "mb_get_ticket_logs": _S(("ticketid",)),
    "mb_list_ticket_categories": _S(),
    "mb_list_ticket_priorities": _S(),
    "mb_list_ticket_performers": _S(),
    "mb_create_ticket": _S(
        ("categoryid", "prioritytypeid"),
        ("fio", "phones", "app", "useruid", "emphasis", "show_ticket_to_user", "order_number", "performafter",
         "performbefore", "set_list"),
        defaults={"fio": "", "phones": "", "app": ""},
        help={"fio": "Contact name.", "useruid": "Subscriber uid to link. The ticket address comes from the user."}),
    "mb_update_ticket": _S(
        ("ticketid",),
        ("fio", "categoryid", "prioritytypeid", "phones", "useruid", "emphasis", "show_ticket_to_user",
         "order_number", "performafter", "performbefore", "set_list"),
        help={"fio": "Contact name.", "useruid": "Subscriber uid to link."}),
    "mb_add_ticket_note": _S(("ticketid", "note")),
    # The status request is assembled by the runtime (it needs the acting staff id), see Runtime._status_params.
    "mb_set_ticket_status": _S(("status",), ("rating",), ticket_ids="set_list",
                               enums={"status": ("open", "in_work", "performed", "closed"), "rating": (1, 2, 3, 4, 5)}),
    "mb_delete_tickets": _S(ticket_ids="enumerate"),
    "mb_delete_ticket_note": _S(("noteid",)),
}
