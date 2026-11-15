# Specification: Mikbill Admin MCP Server

Status: v1 design, verified against the public Mikbill demo (2026-11-15). Implementation not started. This file is the source of truth for scope. Per-endpoint evidence is in `fixtures/`, and the code table is `src/mb_mcp/endpoints.py`.

## Context

AI agents need to work with a Mikbill billing instance through MCP: subscriber lookup and history, provisioning dictionaries, routine edits, and (gated) billing and account-state changes. The upstream PHP class `kagatan/mb-admin-api` wraps 53 public methods, but its parameter docs are sparse and several wrappers do not work as written. This spec defines a Python MCP server that exposes the Mikbill admin endpoints with explicit scopes, dry-run, and approval for every unsafe action.

Decisions:
- Language: Python, official `mcp` SDK, `httpx`.
- Transport: stdio and Streamable HTTP.
- Parameter schemas: from the live admin UI (request field names, verified responses on the demo).
- Every change to billing, account state, privileges, or any delete is gated: opt-in scope, dry-run, approval token, reason.

## Scopes

A tool is registered only when its scope is granted. Gated tools always go through dry-run and approval.

| Scope | Default | Gated | Tools | Contents |
|---|---|---|---|---|
| `read` | on | no | 26 | User search with the admin UI's filters; online users; lookups, histories, dictionaries, system options; tickets, ticket notes and logs; ticket category, priority and performer lookups |
| `write` | on | no | 9 | Create and edit users; add user devices; sticky notes; kick user; create and update tickets, ticket notes, ticket status |
| `account_state` | off | yes | 15 | Freeze; bulk enable, disable, unfreeze, enable internet; disconnect; soft deletes (single, bulk, disconnected); restore a deleted user; full delete; sticky note, ticket, ticket note and user device deletes |
| `finance` | off | yes | 13 | Payments (single, bulk); tariff change; flex tariff (reactivate); packet add, edit, delete; personal service set and unset; credit remove (single, bulk); credit issue (bulk); voucher use |
| `admin` | off | yes | 32 | User groups; address objects (settlements, neighborhoods, lanes, houses); infrastructure objects (sectors, switch types, switches, device types, IP pools, real IPs, NAS). Every create, edit and delete |

Total: 95 tools. Blocked and denied tools are registered (see Blocked and denied).

**Delete rule:** every delete is gated, regardless of entity type. That includes the single-user soft delete `mb_delete_user`, which was briefly a plain `write` tool and was moved back to `account_state`.

**Admin rule:** all changes to address objects (settlements, neighborhoods, lanes, houses) and infrastructure objects (sectors, switch types, switches, device types, IP pools, real IPs, NAS) need the `admin` scope and go through dry-run and approval: every create, edit and delete. The `write` scope is only users, user devices, sticky notes and kick.

**Not in v1:**
- `consoleMe` and `/extjs/options/consolecommands` (console execution)
- `auth`, `authBilling` (client internals)
- Discovered endpoints not yet verified or not decided: `delippoolsipflex`, `nagrasyncsubscriptions` (see Discovered endpoints)

## Tool list

### read (26)

| Tool | Upstream | URI |
|---|---|---|
| `mb_check_session` | checkLoggedIn | `/json/users/getcountfl` |
| `mb_count_users` | getCountUsers | `/json/users/getcountfl` |
| `mb_search_users` | getUsers | `/json/users/searchflex` |
| `mb_list_online_users` | (discovered) | `/json/users/inetonlinefl` |
| `mb_get_user` | getUser | `/json/users/getuserdatambp` |
| `mb_get_user_payment_history` | getUsersHistoryPayments | `/json/users/statpaymfl` |
| `mb_get_user_short_history` | getUserShortHistory | `/extjs/users/usershorthistory` |
| `mb_get_user_canvas_stat` | getUserCanvasStat | `/json/users/getusercanvasstat` |
| `mb_get_user_devices` | getUserDevicesByUid | `/json/users/getuserdevicesbyuid` (sent as `useruid`; `uid` returns an empty `{}`) |
| `mb_list_dev_types` | getDevTypesList | `/json/users/getdevtypesfulllist` |
| `mb_list_settlements` | getSettlementsList | `/json/users/settlementslist` |
| `mb_list_neighborhoods` | getNeighborhoodsList | `/json/users/neighborhoodslist` |
| `mb_list_lanes` | getLanesList | `/json/users/getlanesneighborhoods` |
| `mb_list_houses` | getUsersHousesFullList | `/json/users/housesfulllist` |
| `mb_list_sectors` | getSectorList | `/json/users/sectorlistflex` |
| `mb_list_switches` | getSwitchList | `/json/users/switchlistflex` |
| `mb_list_switch_types` | getSwitchTypeList | `/json/options/switchtypelistflex` |
| `mb_list_packets` | getPacketsList | `/json/tarif/gettarifsfl` |
| `mb_list_user_groups` | getUsersGroupsList | `/json/options/usersgroupslist` |
| `mb_get_system_options` | getSystemOptions | `/extjs/users/getsystemoptions` |
| `mb_list_tickets` | none | `/extjs/ticketview/getticketslist2x` | No `success` field; filters applied to the rows by the MCP |
| `mb_get_ticket_notes` | none | `/json/ticketview/getnotes` | ticketid |
| `mb_get_ticket_logs` | none | `/json/ticketview/getlogs` | ticketid |
| `mb_list_ticket_categories` | none | `/json/ticketview/getcategorieslist` | categoryid, maincategoryid, categoryname, description, color, req_* flags |
| `mb_list_ticket_priorities` | none | `/json/ticketview/getprioritiestypeslist` | 1 high, 2 normal, 3 low |
| `mb_list_ticket_performers` | none | `/extjs/ticketview/getPerformerList` | stuffid, login, fio |

### write (9, not gated)

| Tool | Upstream | URI |
|---|---|---|
| `mb_create_user` | createAbonent | `/extjs/users/createabonent` |
| `mb_update_user` | updateUser | `/extjs/users/updateuser` |
| `mb_kick_user` | userKickOnline | `/json/users/userkickfl` |
| `mb_create_sticky_note` | createStickyNotesUser | `/extjs/users/createstickynotesuser` (`uid`, not `useruid`) |
| `mb_add_user_device` | addUserDev | `/extjs/users/adduserdev` |
| `mb_create_ticket` | none | `/extjs/ticketedit/setticket2x` | categoryid, prioritytypeid required |
| `mb_update_ticket` | none | `/extjs/ticketedit/setticket2x` | With ticketid; only the fields sent change |
| `mb_add_ticket_note` | none | `/extjs/ticketedit/addnote` | ticketid, note |
| `mb_set_ticket_status` | none | `/extjs/ticketedit/putticketin` | status open, in_work, performed, closed; ticket_ids; rating with closed |

### account_state (15, gated)

| Tool | Upstream | URI | Param / note |
|---|---|---|---|
| `mb_freeze_user` | setFreezeUser | `/json/users/dofreezeuserfl` | uid, freeze fields |
| `mb_bulk_enable_users` | none | `/json/users/enableusersmulti` | `set_users_list` |
| `mb_bulk_disable_users` | none | `/json/users/disableusersmulti` | `set_users_list` |
| `mb_bulk_unfreeze_users` | none | `/json/users/unfreezeusersmulti` | `set_users_list` |
| `mb_bulk_enable_internet` | none | `/json/users/enableinternetmulti` | `set_users_list` |
| `mb_disconnect_user` | none | `/json/users/otkluserflex` | uid, cena_freeze. Sets state 3 |
| `mb_delete_user` | none | `/json/users/deluserflex` | uid. **Soft delete** |
| `mb_bulk_delete_users` | none | `/json/users/deleteusersmulti` | `set_users_list`. **Soft delete** |
| `mb_delete_disconnected_user` | none | `/json/users/deluserotklflex` | uid, gid, itog_summa, type_list=2. **Soft delete** |
| `mb_delete_user_full` | none | `/json/users/delfulluserflex` | Denied on the demo |
| `mb_delete_sticky_note` | none | `/extjs/users/deletestickynotesuser` | id |
| `mb_restore_user` | none | `/json/users/enableusersmulti` or `/json/users/vkluserflex` | uid; with gid it assigns that tariff (see Restoring a deleted user) |
| `mb_delete_user_device` | none | `/extjs/users/deleteuserdev` | uid (sent as useruid), devid from `mb_get_user_devices` |
| `mb_delete_tickets` | none | `/extjs/ticketedit/deletetickets` | ticket_ids, sent as ticketid0, ticketid1, ... Dry-run lists the tickets |
| `mb_delete_ticket_note` | none | `/extjs/ticketedit/deletenotes` | noteid |

### finance (13, gated)

| Tool | Upstream | URI | Note |
|---|---|---|---|
| `mb_create_payment` | payment | `/json/users/paymentdoflex` | Moves money. uid, bughtypeid, deposit, prim2 |
| `mb_bulk_payment` | none | `/json/users/paymentdomulti` | Moves money on every listed account |
| `mb_change_tariff` | doChangeTarifFlex | `/json/users/dochangetarifflex` | uid, gid |
| `mb_enable_flex_tariff` | vklUserFlex | `/json/users/vkluserflex` | Reactivates a disconnected user (state 3). Needs uid, gid, itog_summa, type_list=2 |
| `mb_add_packet` | addPacket | `/json/tarif/addtarifflex` | tarif (name) |
| `mb_edit_packet` | editPacket | `/json/tarif/updatetariffl` | gid, packet, alias. Other form fields exist |
| `mb_delete_packet` | none | `/json/tarif/deltarifflex` | gid. Check first: `delchecktarifflex` |
| `mb_set_personal_service` | setPersonalServiceUser | `/json/users/setpersonalserviceuser` | uid, serviceid. Service must have a configured portal |
| `mb_unset_personal_service` | none | `/json/users/unsetpersonalserviceuser` | uid, serviceid (activate, code optional) |
| `mb_remove_credit_single` | none | `/json/users/creditnullfl` | uid |
| `mb_bulk_remove_credit` | none | `/json/users/creditnullmulti` | `set_users_list` |
| `mb_bulk_issue_credit` | none | `/json/users/creditmulti` | `set_users_list`, `credit` |
| `mb_use_voucher` | none | `/json/users/usevaucherfl` | uid, code. A valid code adds balance |

### admin (32, gated)

User groups, address objects (settlements, neighborhoods, lanes, houses) and infrastructure objects (sectors, switch types, switches, device types, IP pools, real IPs, NAS): create, edit and delete all need the `admin` scope, dry-run and approval.

| Tool | Upstream | URI | Note |
|---|---|---|---|
| `mb_create_user_group` | createUsersGroup | `/json/options/setusersgroup` | usersgroupname, share, description |
| `mb_update_user_group` | none | `/json/options/setusersgroup` | With `usersgroupid` it updates |
| `mb_delete_user_group` | none | `/json/options/deleteusersgroup` | **Use `delete_list`**. `usersgroupid` always returns "in use" |
| `mb_add_settlement` | addSettlement | `/json/options/addsettlement` | settlementname |
| `mb_edit_settlement` | editSettlement | `/json/options/editsettlement` | settlementid, settlementname |
| `mb_delete_settlement` | none | `/json/options/deletesettlement` | settlementid. Dry-run runs `checkdeletesettlement` (Mikbill does not enforce it) |
| `mb_add_neighborhood` | addNeighborhood | `/json/options/addneighborhood` | settlementid, neighborhoodname |
| `mb_edit_neighborhood` | editNeighborhood | `/json/options/editneighborhood` | settlementid, neighborhoodid, neighborhoodname |
| `mb_delete_neighborhood` | none | `/json/options/deleteneighborhood` | neighborhoodid. Dry-run runs `checkdeleteneighborhood` (not enforced) |
| `mb_add_lane` | addLane | `/json/options/addlanesflex` | settlementid, lane |
| `mb_edit_lane` | editLane | `/json/options/editlaneflex` | settlementid, laneid, lane |
| `mb_delete_lane` | none | `/json/options/dellaneflex` | laneid. Dry-run runs `checkdellane` (not enforced) |
| `mb_add_house` | addHouse | `/json/options/addhouseflex` | house, laneid, neighborhoodid, porches, floors, app_count_id, postal_index |
| `mb_edit_house` | editHouse | `/json/options/edithouselaneflex` | houseid plus the add fields |
| `mb_delete_house` | deleteHouse | `/json/options/delhouseflex` | `houseid` only. Blocked by design (see below) |
| `mb_add_sector` | addSector | `/json/options/sectoraddflex` | sector |
| `mb_edit_sector` | editSector | `/json/options/sectoreditflex` | sectorid, sector |
| `mb_delete_sector` | none | `/json/options/sectordelflex` | sectorid |
| `mb_add_switch_type` | addSwitchType | `/json/options/addswitchtypeflex` | Works with params (the upstream wrapper sends none) |
| `mb_edit_switch_type` | editSwitchType | `/json/options/editswithchtypeflex` | Upstream spelling kept |
| `mb_delete_switch_type` | delSwitchType | `/json/options/delswitchtypeflex` | swtypeid |
| `mb_add_switch` | addSwitch | `/json/options/addswitchflex` | Works with params (the upstream wrapper sends none) |
| `mb_edit_switch` | editSwitch | `/json/options/editswitchflex` | swid |
| `mb_delete_switch` | delSwitch | `/json/options/delswitchflex` | swid |
| `mb_add_dev_type` | addDevType | `/json/users/adddevtype` | devtypename required |
| `mb_delete_dev_type` | none | `/json/users/deletedevtype` | devtypeid, **sent as `delete_list`**: a plain id is a silent no-op. A type that a device uses is refused |
| `mb_add_ip_pool` | none | `/json/options/addippoolsflex` | poolname |
| `mb_delete_ip_pool` | none | `/json/options/delippoolsflex` | poolname |
| `mb_add_real_ip_block` | none | `/json/options/addrealipflex` | localip, netmask, cidrmask, hostmin, hostmax, hosts |
| `mb_delete_real_ip` | none | `/json/options/delrealipflex` | real (one address per call) |
| `mb_add_nas` | none | `/json/options/nasaddflex` | nasname, nasip, secret, shortname |
| `mb_delete_nas` | none | `/json/options/nasserverdelflex` | nasid |

## User search

`mb_search_users` mirrors the search tab of the admin UI (`/json/users/searchflex`). It takes at least one of these filters; everything is verified on the demo (fixtures/search_and_online_verified.json).

| Argument | Meaning |
|---|---|
| `uid` (integer) | Exact subscriber uid. Mikbill ignores every other filter when `uid` is set, so the tool refuses it together with other filters. Only whole numbers are accepted: Mikbill reads `"02_122"` as 2. Contract numbers cannot be searched. |
| `user`, `address`, `phone`, `local_ip`, `sticky_note`, `app`, `switchport`, `pon_central_closure_txt`, `pon_connection_box_txt` (text) | Substring match. `user` covers login, name, note and company; `phone` covers phone and e-mail; `local_ip` covers IP, MAC and device serial; `address` matches street and house text, not the city name. |
| `settlementid`, `neighborhoodid`, `laneid`, `houseid`, `gid`, `swid`, `sectorid` (integer) | Place, tariff, switch and sector ids from the list tools. |
| `dogovor` (0 to 3) | Contract state: no data, active, suspended, closed. |
| `states` (array) | Any of `normal`, `disabled`, `frozen`, `deleted`. Omitted means every state, **deleted subscribers included** (645 of 905 on the demo). |
| `user_labels` (array of ids) | Label ids. |
| `internet` | `on` or `off`. Omitted means both. |
| `match` | `all` (default, every filter must match) or `any`. `any` is refused together with `states` or `internet`: with `op=0` Mikbill ORs those two selectors into the result (gid 38 plus state normal returned 61 rows, the union) instead of narrowing it. |
| `limit` | Rows returned, default 100, maximum 1000. |

Mikbill does not page the result and returns every subscriber for an empty search, so an empty search is refused and the rows are capped by `limit` (`totalCount` and `truncated` say what was left out). Rows are full subscriber records (personal data).

`housingid` (a UI filter) is not exposed: the demo has no housings, so its values could not be verified.

## Online users

`mb_list_online_users` (`/json/users/inetonlinefl`, read) lists subscribers with an internet session right now: login, IP, session start and duration, traffic counters, tariff, switch and address. Mikbill takes no parameters and ignores filters, so the optional `useruid`, `user`, `gid`, `sectorid`, `swid` and `settlementid` are applied to the returned rows, and `limit` (default 200) caps them.

## Tickets

Nine tools cover the ticket endpoints: reads in `read`, create, update, notes and status in `write`, deletes gated in `account_state` (the same place as the sticky note delete). Evidence is in `fixtures/tickets_verified.json`, captured on the demo with MCPTEST tickets that were deleted afterwards.

What the endpoints do, and what the MCP does about it:

- **`getticketslist2x` has no `success` field** (`{data, counters}`). The client accepts that envelope for this endpoint only (`no_success_key`). Everywhere else a missing `success` is still an error.
- **It ignores filter parameters** (status, category, priority, ticketid, paging all returned every ticket). `mb_list_tickets` takes optional `statustypeid`, `categoryid`, `prioritytypeid` and `ticketid`, applies them to the returned rows, and never sends them. `counters` in the result are unfiltered. Each row already embeds its notes.
- **Create and update share `setticket2x`.** The creator and note authors come from the session, so the UI's `employed_by_stuffid` and `note_author` are not sent. `useruid` links a subscriber, and the ticket's address then comes from the user's house.
- **Status changes (`putticketin`) need the acting staff id.** A request with only `set_list` and `statustypeid` works for "open" and fails with error 1202 for the other statuses. The tool builds the full request the admin UI sends, using the session's own staff id (`data.stuff_propers.stuffid` from `/json/index/getstuffproper`, fetched once and cached). `status` is `open` (1), `closed` (2), `in_work` (3) or `performed` (4); `rating` (1 to 5) is only valid with `closed`. One call can change several tickets (`ticket_ids`, at most `MB_MAX_BULK`). The result reads the tickets back (`state_after`).
- **Deletes** send `ticketid0`, `ticketid1`, ... The dry-run lists each ticket with its status, category, priority, rating and creation date (no names or phone numbers), and the result shows `listed: false` for tickets that are gone. A deleted ticket cannot be restored through any endpoint found.
- **Ticket notes** are treated as personal data: they are masked in the audit log.

Lookups: `mb_list_ticket_categories`, `mb_list_ticket_priorities` and `mb_list_ticket_performers` give the category, priority and performer ids that `mb_create_ticket` and `mb_update_ticket` take (`set_list` is a comma list of performer staff ids).

## Restoring a deleted user

`mb_restore_user` (gated, `account_state`) brings a soft-deleted user (state 4, `deletedtable` 1) back. Two verified paths, chosen by whether a tariff is given (evidence in `fixtures/restore_devices_lookups_verified.json`):

- **No `gid`: `enableusersmulti`** (`set_users_list`). The user returns to state 1 on the same tariff. It worked for users deleted by `deluserflex` and for users that were disconnected and then deleted by `deluserotklflex` (they come back to state 1, not 3).
- **With `gid`: `vkluserflex`** (`uid`, `gid`, `itog_summa`, `type_list` 2). It works directly from the deleted state: no separate enable step. The user returns to state 1 on the given tariff. This is the way to restore a user whose tariff is gone (gid 0, tariff "Error", which is what a scheduled tariff change leaves). It can charge: the demo deposit dropped by the tariff's first daily fee.
- **Preconditions and checks:** the user must be deleted, otherwise `precondition_failed` (`vkluserflex` on an active user fails with `DUPLICATE_LOGIN`). The dry-run shows the user's current state and which request will be sent. After the call the state is read back, and the tool fails if the user is still deleted.


The upstream UI uses two different parameter styles for deletes. They do not behave the same:

- `delete_list` is what the UI's generic delete form sends. For user groups it works. For houses it returns `success: true` but deletes nothing (houses 90 and 92 were still listed afterwards), so it is a silent no-op there. An earlier note in this repo called it an in-use-check bypass; that was wrong.
- The id field (`houseid`, `swid`, and so on) takes the checked path. For houses, it returns error 8 when users still reference the house.

Rules for the MCP:
- `mb_delete_house` sends `houseid` only. Never `delete_list`.
- `mb_delete_dev_type` is the reverse of houses: a plain `devtypeid` is the silent no-op (success, nothing deleted) and `delete_list` really deletes, refusing a type that a device still uses. Found by the contract run leaking a device type each time; the tool now sends `delete_list`.
- `mb_delete_user_group` sends `delete_list`. Dry-run must count users with that `usersgroupid` first, because the in-use check was not reproduced with `delete_list`.
- Every delete dry-run runs the check endpoint where one exists and lists what would be affected.
- **Mikbill does not enforce its own dependency checks for settlements, neighborhoods and lanes.** On the demo, the check endpoints report dependents ("can't delete lane with houses"; `checkdeletesettlement` returns error 2), but `dellaneflex`, `deleteneighborhood` and `deletesettlement` delete anyway and leave the houses pointing at nothing (lane name empty, neighborhood 0, settlement empty). So these three deletes stay gated in `admin`, and their dry-runs run `checkdellane`, `checkdeleteneighborhood` and `checkdeletesettlement` so the dependents are visible before approval. Moving them to `write` was considered and rejected for this reason.

## Soft delete

- `deluserflex`, `deleteusersmulti`, and `deluserotklflex` mark the account as deleted: state `4`, `deletedtable` `1`. The row stays in the database.
- Tool descriptions say "soft delete" and name the effect. The output includes the state read back after the call.
- All three soft-delete tools (`mb_delete_user`, `mb_bulk_delete_users`, `mb_delete_disconnected_user`) are gated in `account_state`: dry-run, approval token and reason. Bulk dry-runs list every uid.
- Dry-run shows the target uid(s), current state, and count. Bulk dry-run lists every uid.
- Restoration: see Restoring a deleted user (`mb_restore_user`).

## Blocked and denied

Blocked and denied tools are registered in their scope. Calling one returns the mapped error. Dry-run still runs any check endpoint and reports the blocker without executing.

| Tool | Status | Cause and fix |
|---|---|---|
| `mb_delete_house` | Blocked by design | `houseid` returns error 8 when users reference the house. This is the correct protection. Do not use `delete_list` for houses: it reports success and deletes nothing. Demo houses 90 (`MCPTEST_H1b`) and 92 (`MCPTEST_H3`) remain because users reference them. A house with no users deletes fine via `houseid` |
| `mb_delete_user_full` | Denied | "Denied by settings" on the demo. Maps to `denied_by_settings`. Registered; returns this code where the installation forbids it |
| `mb_set_personal_service` | Blocked for serviceid 115 only | 115 has service portal 18 (Wink), which is not configured on the demo. serviceid 101 (portal 0) works and is verified. It also fails with the same "service_portal ... not found" message after `mb_change_tariff`: the change is scheduled, so the user's gid reads 0 until it takes effect (found by the contract run) |

Previously blocked, now resolved:
- `mb_enable_flex_tariff`: the earlier failure was a wrong precondition. It works on a disconnected user with `type_list=2` and `itog_summa`.
- `mb_delete_user_group`: the earlier failure used `usersgroupid`. `delete_list` works (verified on several groups). Mikbill reuses group ids, and an old deleted subscriber can still reference a reused id (demo group 8 and subscriber 233): the delete then returns error 3, mapped to `conflict`.

## Error mapping

- `success: false` with "Denied by settings" → `denied_by_settings`
- error 8 on a delete → `conflict` (in use)
- error 3 → `conflict` (seen on user-group delete: "used by subscribers"; the message text is localized, the code is not)
- error 2 with `DUPLICATE_LOGIN` → `precondition_failed` (on `vkluserflex`, the user is not disconnected)
- error 27 → `precondition_failed` (service portal not configured)
- error 1 with "Не указан обязательный параметр" → `validation` (missing required parameter, with the key in `errorKey`)
- error 1202 → `upstream_error` (generic, logged on the server)
- `success: false` with `error: 1403` / `UnAuth` → `auth_failed` (re-login once, then error)
- Missing `success` key, empty body, or HTTP non-200 → `upstream_error`

## Gated-tool mechanics

- Dry-run is the default. It returns the exact request, a target summary (uids, count, total amount for payments, check results for deletes), and a one-time `approval_token` valid 5 minutes, bound to a hash of the params.
- A second call with `dry_run: false` and the token executes the request. The token is single use.
- `reason` is required on every gated call and written to the audit log.
- Bulk tools: dry-run lists every uid. Payment tools: dry-run shows the total amount.

## Authentication and session

- Configuration from env vars only: `MB_BASE_URL`, `MB_LOGIN`, `MB_PASSWORD` (plaintext; MD5-hashed in memory, as the protocol requires), `MB_SCOPES`, `MB_CA_BUNDLE`, `MB_TIMEOUT_CONNECT=5`, `MB_TIMEOUT_READ=30`, `MB_MAX_BULK=50` (cap on uids per bulk call), `MB_AUDIT_FILE` (optional JSON-lines audit log).
- One `httpx.AsyncClient` with a cookie jar holds the session. Login runs lazily.
- The session cookie is sufficient. Calls without it return HTTP 200 with `success: false`, error 1403. The JWT header is not needed by the UI and is not sent.
- On auth failure, re-login once and retry once. A second failure is an error.
- TLS verification is on by default. `MB_CA_BUNDLE` supports private CAs. Disabling needs `MB_INSECURE_TLS=1` and logs a warning at startup.
- The MCP acts as one Mikbill admin account. Use a dedicated, least-privileged account. Scopes limit the MCP; they do not replace Mikbill's permission model.

## Transport

- The transport is chosen with `MCP_TRANSPORT` (`stdio` or `http`; default `stdio`), `--transport`, or `--http`. The command line wins. The settings marked HTTP-only below (`MCP_HTTP_HOST`, `MCP_HTTP_PORT`, `MCP_HTTP_BEARER_TOKEN`, `MB_HTTP_TOKENS`, `MCP_ALLOWED_ORIGINS`, `MCP_ALLOWED_HOSTS`) are read only in http mode and are ignored, not validated, in stdio mode, so one env file or one image serves both.
- **stdio** (default): `python -m mb_mcp`, env from the MCP client config.
- **Streamable HTTP**: `python -m mb_mcp --http`. Host and port come from `MCP_HTTP_HOST` and `MCP_HTTP_PORT` (defaults `127.0.0.1` and `8765`), or from `--host` and `--port`. Binds to localhost by default. Remote access goes through a TLS reverse proxy. The Docker image sets `0.0.0.0` and `8000`.
- `GET /health` (and `HEAD`) answers `200 {"status":"ok"}` with no token and no Origin check. It is a liveness check for the HTTP server only: it does not call Mikbill and returns no configuration. The Docker health check uses it. Every other path needs a token.
- HTTP requires `Authorization: Bearer <token>`. Each token has its own scope list. Config stores SHA-256 hashes. Missing or invalid token: 401. Disallowed `Origin`: 403. A token without a tool's scope does not see the tool in `tools/list`, and calling it returns a `scope_denied` tool error (not an HTTP 403). Tokens are configured with `MB_HTTP_TOKENS`, a JSON list of `{id, sha256, scopes}`, and/or `MCP_HTTP_BEARER_TOKEN`, one plain token (at least 16 characters; only its SHA-256 is kept) that gets every scope in `MB_SCOPES`, principal id `bearer` in the audit log; `MCP_ALLOWED_ORIGINS` lists allowed origins. The `Host` header must be `<bind host>:<port>`, `127.0.0.1:<port>`, `localhost:<port>` or listed in `MCP_ALLOWED_HOSTS` (otherwise 421), as DNS-rebinding protection. The repository ships a `Dockerfile`: one image for both modes (http on 8000 by default, stdio with `-e MCP_TRANSPORT=stdio`; the health check requests `/health` with the image's Python in http mode; the image has no curl or wget) and a Docker smoke test in `tests/smoke`.
- Validate the `Origin` header and reject unknown origins.
- HTTP mode shares one Mikbill session across clients. Scope checks run per token before any upstream call.

## Tool contract

- Names: `mb_` prefix, snake_case, verb first.
- Input: JSON Schema per tool, from the fixtures. Undocumented fields are rejected.
- Output envelope:
  ```json
  {"ok": true, "status": 200, "data": {...}}
  {"ok": false, "error": {"code": "upstream_error|auth_failed|timeout|validation|scope_denied|approval_required|conflict|precondition_failed|denied_by_settings", "message": "..."}}
  ```
- `mb_get_user_payment_history` requires at least one filter (`uid`, or `from_date`/`to_date`). `mb_search_users` requires at least one search filter (see User search).
- Tools that return many rows take `limit` (`mb_search_users` default 100, `mb_list_online_users` default 200, maximum 1000). The result has `totalCount` (rows that matched, before the limit) and `truncated`.
- Tool descriptions are in English. Mikbill field names keep their original spelling.

## Redaction and logging

- User records return plaintext credentials: `password`, `router_pass`, `router_wep_pass`, and `pass` in history `postauth` rows. Redact these by default. Optional reveal needs its own scope and is not in v1.
- Personal fields (phone, passport, INN, address, email, date of birth) are redacted from logged bodies. Field allowlists apply to reads.
- Logs never contain the password, session cookies, JWT, bearer tokens, or the MD5 hash.
- Audit entries for writes, gated calls, and auth failures: timestamp, tool, scope, token id, target uid or id, `reason`, outcome.

## Discovered endpoints

Found in the admin UI and verified on MCPTEST records (details in `fixtures/discovered_and_blockers.json`):

| Endpoint | Tool | Result | Scope |
|---|---|---|---|
| `otkluserflex` | `mb_disconnect_user` | Verified | account_state |
| `deluserotklflex` | `mb_delete_disconnected_user` | Verified (soft delete) | account_state |
| `vkluserflex` | `mb_enable_flex_tariff` | Verified (was blocked) | finance |
| `unsetpersonalserviceuser` | `mb_unset_personal_service` | Verified | finance |
| `usevaucherfl` | `mb_use_voucher` | Wrong-code path verified. Valid-code path not run | finance |
| `delippoolsflex` | `mb_delete_ip_pool` | Verified | admin |
| `delrealipflex` | `mb_delete_real_ip` | Verified | admin |
| `nasserverdelflex` | `mb_delete_nas` | Verified | admin |
| `addippoolsflex` | `mb_add_ip_pool` | Verified | admin |
| `addrealipflex` | `mb_add_real_ip_block` | Verified | admin |
| `nasaddflex` | `mb_add_nas` | Verified | admin |

Not in v1 yet:
- `delippoolsipflex`: returned `{}` for an invalid input. Needs valid input before it can be verified.
- `nagrasyncsubscriptions`: calls an external Nagra integration. Not called from the demo.

## Parameter capture and fixtures

- `fixtures/writes_verified.json`: request fields, responses, and errors for each write tool run on the demo.
- `fixtures/restore_devices_lookups_verified.json`: the ticket lookups, the device tools (`useruid`, device rows, `deleteuserdev`), the device type delete finding, and both restore paths.
- `fixtures/tickets_verified.json`: the nine ticket tools, with the list endpoint's missing `success` field and ignored filters, and the full status request.
- `fixtures/discovered_and_blockers.json`: discovered endpoints, blocker investigations, and demo side effects.
- `fixtures/<method>.json`: read endpoints from the UI.
- `fixtures/*Multi.json`, `fixtures/delUserFlex.json`, `fixtures/delFullUserFlex.json`: bulk and delete endpoints. Their status fields are stale; see `writes_verified.json`.
- `fixtures/_source_hints.json`: field names from UI code, unverified. Superseded where verified fixtures exist.
- `fixtures/index.json`: status per tool, blocked and denied list, cleanup leftovers.

Fixtures store field names, types, and synthetic examples. No real personal data. HAR files stay out of git.

## Demo side effects and leftovers

Verification left records on the demo at the user's request, including:
- Soft-deleted or disconnected MCPTEST subscribers.
- Active MCPTEST subscribers (uids 860, 861, 862).
- Houses `MCPTEST_H1b` (90) and `MCPTEST_H3` (92) still exist, referenced by MCPTEST users. Nothing is orphaned.

See `fixtures/index.json` (`cleanup_leftovers`) and `fixtures/discovered_and_blockers.json` (`demo_side_effects_from_this_round`). Contract tests reuse the MCPTEST naming and should not touch other demo data.

## Project layout

Location: `C:\Users\sasha\git\campus\mikbill-mcp\`. `git init` happens when implementation starts.

```
mikbill-mcp/
  pyproject.toml
  SPEC.md
  README.md
  .gitignore
  fixtures/
  src/mb_mcp/
    __init__.py
    __main__.py          # CLI: stdio or --http (stub)
    endpoints.py         # endpoint table: 80 tools, scope, gated, status (done)
    config.py            # env parsing, scopes, token hashes (planned)
    client.py            # httpx session, login, re-login, normalize (planned)
    server.py            # FastMCP app, registers tools by scope (planned)
    gating.py            # dry-run and approval tokens (planned)
    audit.py             # audit log, redaction (planned)
    http_auth.py         # bearer and Origin checks (planned)
  tests/
    unit/                # respx-mocked upstream (planned)
    contract/            # opt-in, demo or own test instance only (planned)
```

## Verification

1. **Unit tests** (`pytest`, `respx`): login and MD5 hashing; one re-login on failure; `success: false` mapping; each error code in the mapping table, using recorded responses from the fixtures; scope registration (counts per scope: 26, 9, 15, 13, 32); dry-run makes no upstream POST; approval token single use and bound to params; bulk dry-run lists every uid; missing `reason` rejected; delete tools use the parameter listed in this spec; `mb_delete_house` never sends `delete_list`; bearer and Origin checks; redaction.
2. **Contract tests** (opt-in; demo or own test instance only, MCPTEST records only): the verified sequence through the MCP. Soft delete leaves state 4. Blocked tools return their mapped codes.
3. **MCP inspector:** `npx @modelcontextprotocol/inspector python -m mb_mcp` lists tools per scope.
4. **Client config check:** add to Claude Code or Claude Desktop with env vars; search a test user by `uid`.
5. **HTTP mode:** no token gives 401; read-only token calling `mb_create_user` gives 403; gated call without `reason` is rejected.
6. **Security:** TLS verification on by default; no tool in any scope wraps console execution.

## Assumptions

1. Every delete is gated, including reference data and the single-user soft delete. Confirmed by the user.
2. `mb_delete_user_full` stays registered and returns `denied_by_settings` where denied. Confirmed by the user.
3. Discovered endpoints are included after verification. Confirmed by the user. Pending ones are listed above.
4. Packet catalog edits, personal service, voucher use, and user-group create/update are gated because they change billing or privileges.
5. Upstream is proprietary. Confirm before any derived code ships.
6. The demo may reset or reject some writes. Endpoints it cannot exercise need a check on a private test instance.
