"""Per-tool request schemas, taken from fixtures/ (verified on the demo).

Field names are Mikbill's own. `defaults` are filled when the caller omits a field, `fixed` are
always sent, `rename` maps a tool argument to the Mikbill parameter name, and `uids` tools take
an array that is sent as `set_users_list=<uid,uid,...>`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCALAR_TYPES = ["string", "integer", "number", "boolean"]


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
    integers: tuple[str, ...] = ()      # arguments that must be whole numbers (a string of digits is accepted)
    help: dict[str, str] = field(default_factory=dict)   # per-argument descriptions for the tool definition
    user_search: bool = False           # the Mikbill user search form: adds states, user_labels, internet and match
    row_limit: int | None = None        # default of the `limit` argument that caps returned rows; None: no such argument

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


def _S(required: tuple[str, ...] = (), optional: tuple[str, ...] = (), **kw: Any) -> ToolSchema:
    return ToolSchema(required=required, optional=optional, **kw)


_FREEZE_OPT = ("do_return_abonplata", "freeze_do_ever", "freeze_date_start", "freeze_date_stop", "cena_freeze",
               "cena_sutok_freeze", "count_free_freeze", "count_free_freeze_use", "cena_unfreeze")

SCHEMAS: dict[str, ToolSchema] = {
    # ---- read ----
    "mb_check_session": _S(),
    "mb_count_users": _S(),
    # The same filters as the admin UI's search tab. At least one filter is required: an empty search returns every
    # subscriber (905 on the demo, deleted ones included), and Mikbill does not page the result.
    "mb_search_users": _S(
        (), tuple(SEARCH_HELP),
        integers=("uid", "settlementid", "neighborhoodid", "laneid", "houseid", "gid", "swid", "sectorid", "dogovor"),
        enums={"dogovor": SEARCH_CONTRACT_STATES}, help=SEARCH_HELP, user_search=True, row_limit=100),
    "mb_get_user": _S(("uid",)),
    "mb_get_user_payment_history": _S(("uid",), ("from_date", "to_date")),
    "mb_get_user_short_history": _S(("uid",)),
    "mb_get_user_canvas_stat": _S(("uid",)),
    # Mikbill wants the parameter named useruid here; `uid` returns an empty {} even when the user has devices.
    "mb_get_user_devices": _S(("uid",), rename={"uid": "useruid"}, allow_empty=True),
    "mb_list_dev_types": _S(),
    "mb_list_settlements": _S(),
    "mb_list_neighborhoods": _S(),
    "mb_list_lanes": _S(),
    "mb_list_houses": _S(),
    "mb_list_sectors": _S(),
    "mb_list_switches": _S(),
    "mb_list_switch_types": _S(),
    "mb_list_packets": _S((), ("page", "start", "limit")),
    "mb_list_user_groups": _S(),
    # Mikbill ignores every parameter for inetonlinefl (verified), so the filters are applied to the rows.
    "mb_list_online_users": _S(
        (), ("useruid", "user", "gid", "sectorid", "swid", "settlementid"),
        row_filters=("useruid", "user", "gid", "sectorid", "swid", "settlementid"),
        integers=("useruid", "gid", "sectorid", "swid", "settlementid"), row_limit=200,
        help={"useruid": "Only this subscriber uid.", "user": "Only this exact login.", "gid": "Only this tariff id.",
              "sectorid": "Only this sector id.", "swid": "Only this switch id.",
              "settlementid": "Only this settlement id."}),
    "mb_get_system_options": _S(),
    # ---- write ----
    "mb_create_user": _S(
        ("user", "password", "fio", "gid", "sectorid", "settlementid", "neighborhoodid", "laneid", "houseid", "app"),
        ("phone", "email", "prim", "use_installments", "lane_name", "lane_create", "house_name", "house_create"),
        defaults={"phone": "", "email": "", "prim": "", "use_installments": 0, "lane_name": "", "lane_create": 0,
                  "house_name": "", "house_create": 0}),
    "mb_update_user": _S(("useruid",), ("fio", "prim", "email", "phone", "blocked", "user", "gid", "usersgroupid",
                                         "password")),
    "mb_kick_user": _S(("uid",)),
    "mb_create_sticky_note": _S(("uid", "description"), ("prim",)),
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
    # ---- account_state ----
    "mb_freeze_user": _S(("uid", "do_freeze_now"), _FREEZE_OPT,
                         defaults={"do_return_abonplata": 0, "freeze_do_ever": 0, "cena_freeze": 0,
                                   "cena_sutok_freeze": 0, "count_free_freeze": 0, "count_free_freeze_use": 0,
                                   "cena_unfreeze": 0}),
    "mb_bulk_enable_users": _S(uids=True),
    "mb_bulk_disable_users": _S(uids=True),
    "mb_bulk_unfreeze_users": _S(uids=True),
    "mb_bulk_enable_internet": _S(uids=True),
    "mb_disconnect_user": _S(("uid",), ("cena_freeze",), defaults={"cena_freeze": 0}),
    "mb_delete_user": _S(("uid",)),
    "mb_bulk_delete_users": _S(uids=True),
    "mb_delete_disconnected_user": _S(("uid", "gid"), ("itog_summa",), defaults={"itog_summa": 0},
                                      fixed={"type_list": 2}),
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
    "mb_restore_user": _S(("uid",), ("gid", "itog_summa")),
    # ---- finance ----
    "mb_create_payment": _S(("uid", "bughtypeid", "deposit", "prim2")),
    "mb_bulk_payment": _S(("bughtypeid", "deposit", "prim2"), uids=True),
    "mb_change_tariff": _S(("uid", "gid")),
    "mb_enable_flex_tariff": _S(("uid", "gid"), ("itog_summa",), defaults={"itog_summa": 0}, fixed={"type_list": 2}),
    "mb_add_packet": _S(("tarif",)),
    "mb_edit_packet": _S(("gid", "packet"), ("alias",)),
    "mb_delete_packet": _S(("gid",)),
    "mb_set_personal_service": _S(("uid", "serviceid")),
    "mb_unset_personal_service": _S(("uid", "serviceid"), ("activate", "code")),
    "mb_remove_credit_single": _S(("uid",)),
    "mb_bulk_remove_credit": _S(uids=True),
    "mb_bulk_issue_credit": _S(("credit",), uids=True),
    "mb_use_voucher": _S(("uid", "code")),
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
    "mb_list_tickets": _S((), ("statustypeid", "categoryid", "prioritytypeid", "ticketid"),
                          row_filters=("statustypeid", "categoryid", "prioritytypeid", "ticketid"),
                          no_success_key=True),
    "mb_get_ticket_notes": _S(("ticketid",)),
    "mb_get_ticket_logs": _S(("ticketid",)),
    "mb_list_ticket_categories": _S(),
    "mb_list_ticket_priorities": _S(),
    "mb_list_ticket_performers": _S(),
    "mb_create_ticket": _S(
        ("categoryid", "prioritytypeid"),
        ("fio", "phones", "app", "useruid", "emphasis", "show_ticket_to_user", "order_number", "performafter",
         "performbefore", "set_list"),
        defaults={"fio": "", "phones": "", "app": ""}),
    "mb_update_ticket": _S(
        ("ticketid",),
        ("fio", "categoryid", "prioritytypeid", "phones", "useruid", "emphasis", "show_ticket_to_user",
         "order_number", "performafter", "performbefore", "set_list")),
    "mb_add_ticket_note": _S(("ticketid", "note")),
    # The status request is assembled by the runtime (it needs the acting staff id), see Runtime._status_params.
    "mb_set_ticket_status": _S(("status",), ("rating",), ticket_ids="set_list",
                               enums={"status": ("open", "in_work", "performed", "closed"), "rating": (1, 2, 3, 4, 5)}),
    "mb_delete_tickets": _S(ticket_ids="enumerate"),
    "mb_delete_ticket_note": _S(("noteid",)),
}
