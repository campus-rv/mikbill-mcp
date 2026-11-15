from collections import Counter

from mb_mcp.config import GATED_SCOPES
from mb_mcp.endpoints import ALL, EXCLUDED, PENDING
from mb_mcp.schemas import SCHEMAS


def test_tool_counts_per_scope():
    counts = Counter(ep.scope for ep in ALL)
    assert counts == {"read": 27, "write": 12, "account": 12, "finance": 15, "admin": 33}
    assert len(ALL) == 99


def test_names_and_uris_are_sane():
    names = [ep.tool for ep in ALL]
    assert len(names) == len(set(names))
    assert all(n.startswith("mb_") for n in names)
    assert all(ep.uri.startswith(("/json/", "/extjs/")) for ep in ALL)


def test_every_tool_has_a_schema_and_no_extras():
    assert {ep.tool for ep in ALL} == set(SCHEMAS)


# The two exceptions to "gated exactly in the gated scopes": a read that holds admin-only data, and a delete in the
# write scope that cannot be undone.
READ_ONLY_OUTSIDE_READ = {"mb_get_system_options"}
GATED_IN_WRITE = {"mb_delete_tickets"}


def test_gated_exactly_for_gated_scopes():
    for ep in ALL:
        if ep.tool in READ_ONLY_OUTSIDE_READ:
            assert ep.scope == "admin" and ep.read_only and not ep.gated
        elif ep.tool in GATED_IN_WRITE:
            assert ep.scope == "write" and ep.gated
        else:
            assert ep.gated == (ep.scope in GATED_SCOPES), ep.tool
            assert not ep.read_only, ep.tool


def test_system_options_are_not_in_the_read_scope():
    """Server settings and paths with keys nobody enumerated: not for every read token."""
    ep = next(e for e in ALL if e.tool == "mb_get_system_options")
    assert ep.scope == "admin" and ep.is_read


def test_every_tool_has_a_description_for_the_model_without_maintainer_notes():
    for ep in ALL:
        assert ep.desc and ep.desc[0].isupper() and ep.desc.endswith("."), ep.tool
        for word in ("Redact", "Verified", "verified", "Upstream", "upstream", "MCPTEST", "delete_list", "wrapper",
                     "fixture", "the demo"):
            assert word not in ep.desc, (ep.tool, word)


def test_no_console_execution_is_registered():
    uris = {ep.uri for ep in ALL}
    assert "/json/options/consolme" not in uris
    assert "/extjs/options/consolecommands" not in uris
    assert any(name == "consoleMe" for name, _ in EXCLUDED)


def test_pending_endpoints_are_not_registered():
    uris = {ep.uri for ep in ALL}
    assert not any(uri in uris for uri, _ in PENDING)


# Deletes of these four are in the write scope. Three are ungated (the client asks before the call). Deleting
# tickets cannot be undone and takes a list, so it is gated inside write: a reason, and a preview by default.
WRITE_DELETES = {"mb_delete_sticky_note", "mb_delete_user_device", "mb_delete_tickets", "mb_delete_ticket_note"}


def test_every_delete_is_gated_except_three_in_the_write_scope():
    for ep in ALL:
        if "delete" in ep.tool and ep.tool not in WRITE_DELETES:
            assert ep.gated, ep.tool
    by_name = {ep.tool: ep for ep in ALL}
    for name in WRITE_DELETES:
        assert by_name[name].scope == "write", name
        assert by_name[name].gated == (name in GATED_IN_WRITE), name


def test_soft_deletes_and_full_delete_are_gated_account():
    by_name = {ep.tool: ep for ep in ALL}
    for name in ("mb_delete_user", "mb_bulk_delete_users", "mb_delete_disconnected_user", "mb_delete_user_full"):
        assert by_name[name].scope == "account" and by_name[name].gated, name


# Address objects (settlements, neighborhoods, lanes, houses) and infrastructure objects: every create, edit and
# delete is in the gated admin scope.
ADDRESS_OBJECTS = ("settlement", "neighborhood", "lane", "house")
INFRASTRUCTURE = ("switch", "switch_type", "dev_type", "sector", "ip_pool", "real_ip", "nas")


def test_address_and_infrastructure_objects_are_gated_admin_for_create_edit_and_delete():
    by_name = {ep.tool: ep for ep in ALL}
    for obj in ADDRESS_OBJECTS + INFRASTRUCTURE:
        found = [n for n in by_name if n in (f"mb_add_{obj}", f"mb_edit_{obj}", f"mb_delete_{obj}",
                                             f"mb_add_{obj}_block")]
        assert found, obj
        for name in found:
            assert by_name[name].scope == "admin" and by_name[name].gated, name
    # all three verbs exist for the address objects
    for obj in ADDRESS_OBJECTS:
        for verb in ("add", "edit", "delete"):
            assert f"mb_{verb}_{obj}" in by_name, (verb, obj)


def test_write_scope_is_users_devices_notes_kick_tickets_and_their_deletes():
    write = {ep.tool for ep in ALL if ep.scope == "write"}
    assert write == {
        "mb_update_user", "mb_kick_user", "mb_create_sticky_note", "mb_add_user_device",
        "mb_create_ticket", "mb_update_ticket", "mb_add_ticket_note", "mb_set_ticket_status",
        "mb_delete_sticky_note", "mb_delete_user_device", "mb_delete_tickets", "mb_delete_ticket_note",
    }
    assert {ep.tool for ep in ALL if ep.scope == "write" and ep.gated} == GATED_IN_WRITE


def test_delete_house_never_uses_delete_list():
    s = SCHEMAS["mb_delete_house"]
    assert s.required == ("houseid",)
    assert "delete_list" not in s.rename.values()


def test_group_delete_uses_delete_list():
    assert SCHEMAS["mb_delete_user_group"].rename == {"usersgroupid": "delete_list"}


def test_the_account_scope_replaced_account_state():
    from mb_mcp.config import ALL_SCOPES, DATA_SCOPES
    assert "account" in ALL_SCOPES and "account_state" not in ALL_SCOPES
    assert {ep.scope for ep in ALL} == set(ALL_SCOPES) - set(DATA_SCOPES)  # pii holds no tools
    assert {ep.tool for ep in ALL if ep.scope == "account"} == {
        "mb_freeze_user", "mb_bulk_enable_users", "mb_bulk_disable_users", "mb_bulk_unfreeze_users",
        "mb_bulk_enable_internet", "mb_disconnect_user", "mb_bulk_delete_users", "mb_delete_user",
        "mb_delete_disconnected_user", "mb_delete_user_full", "mb_restore_user", "mb_create_user"}


def test_creating_a_subscriber_needs_the_account_scope_and_is_gated_but_not_bulk():
    ep = next(e for e in ALL if e.tool == "mb_create_user")
    assert ep.scope == "account" and ep.gated
    assert not SCHEMAS["mb_create_user"].uids  # a single tool: it executes on the first call, no preview by default


# ---- MCP tool annotations ----------------------------------------------------------------------------------

def test_every_tool_has_annotations_and_a_title():
    from mb_mcp.endpoints import tool_annotations, tool_title
    for ep in ALL:
        a = tool_annotations(ep)
        assert a["open_world_hint"] is False and "read_only_hint" in a, ep.tool
        assert tool_title(ep) and not tool_title(ep).startswith("Mb"), ep.tool


def test_read_tools_are_read_only_and_everything_else_is_not():
    from mb_mcp.endpoints import tool_annotations
    for ep in ALL:
        assert tool_annotations(ep)["read_only_hint"] is ep.is_read, ep.tool
        if ep.is_read:
            assert "destructive_hint" not in tool_annotations(ep)


def test_additive_creates_are_not_destructive_and_deletes_payments_and_updates_are():
    from mb_mcp.endpoints import tool_annotations
    by_name = {ep.tool: ep for ep in ALL}
    for name in ("mb_create_user", "mb_create_ticket", "mb_create_sticky_note", "mb_add_user_device", "mb_add_switch",
                 "mb_add_packet", "mb_create_user_group", "mb_add_ticket_note"):
        assert tool_annotations(by_name[name])["destructive_hint"] is False, name
    for name in ("mb_update_user", "mb_kick_user", "mb_delete_user", "mb_bulk_delete_users", "mb_delete_tickets",
                 "mb_delete_user_device", "mb_create_payment", "mb_bulk_payment", "mb_change_tariff", "mb_freeze_user",
                 "mb_edit_house", "mb_delete_settlement", "mb_set_ticket_status", "mb_bulk_remove_credit"):
        assert tool_annotations(by_name[name])["destructive_hint"] is True, name


def test_idempotent_hint_is_for_deletes_edits_updates_and_setters_only():
    from mb_mcp.endpoints import tool_annotations
    by_name = {ep.tool: ep for ep in ALL}
    for name in ("mb_delete_user", "mb_bulk_delete_users", "mb_edit_house", "mb_update_user", "mb_set_ticket_status",
                 "mb_set_personal_service", "mb_unset_personal_service"):
        assert tool_annotations(by_name[name])["idempotent_hint"] is True, name
    for name in ("mb_create_payment", "mb_bulk_payment", "mb_create_user", "mb_add_switch", "mb_bulk_issue_credit"):
        assert tool_annotations(by_name[name])["idempotent_hint"] is False, name
