from collections import Counter

from mb_mcp.config import GATED_SCOPES
from mb_mcp.endpoints import ALL, EXCLUDED, PENDING
from mb_mcp.schemas import SCHEMAS


def test_tool_counts_per_scope():
    counts = Counter(ep.scope for ep in ALL)
    assert counts == {"read": 26, "write": 9, "account_state": 15, "finance": 13, "admin": 32}
    assert len(ALL) == 95


def test_names_and_uris_are_sane():
    names = [ep.tool for ep in ALL]
    assert len(names) == len(set(names))
    assert all(n.startswith("mb_") for n in names)
    assert all(ep.uri.startswith(("/json/", "/extjs/")) for ep in ALL)


def test_every_tool_has_a_schema_and_no_extras():
    assert {ep.tool for ep in ALL} == set(SCHEMAS)


def test_gated_exactly_for_gated_scopes():
    for ep in ALL:
        assert ep.gated == (ep.scope in GATED_SCOPES), ep.tool


def test_no_console_execution_is_registered():
    uris = {ep.uri for ep in ALL}
    assert "/json/options/consolme" not in uris
    assert "/extjs/options/consolecommands" not in uris
    assert any(name == "consoleMe" for name, _ in EXCLUDED)


def test_pending_endpoints_are_not_registered():
    uris = {ep.uri for ep in ALL}
    assert not any(uri in uris for uri, _ in PENDING)


def test_every_delete_is_gated():
    for ep in ALL:
        if "delete" in ep.tool:
            assert ep.gated, ep.tool


def test_soft_deletes_and_full_delete_are_gated_account_state():
    by_name = {ep.tool: ep for ep in ALL}
    for name in ("mb_delete_user", "mb_bulk_delete_users", "mb_delete_disconnected_user", "mb_delete_user_full"):
        assert by_name[name].scope == "account_state" and by_name[name].gated, name


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


def test_write_scope_is_only_users_devices_notes_kick_and_tickets():
    write = {ep.tool for ep in ALL if ep.scope == "write"}
    assert write == {
        "mb_create_user", "mb_update_user", "mb_kick_user", "mb_create_sticky_note", "mb_add_user_device",
        "mb_create_ticket", "mb_update_ticket", "mb_add_ticket_note", "mb_set_ticket_status",
    }
    assert all(not ep.gated for ep in ALL if ep.scope == "write")


def test_delete_house_never_uses_delete_list():
    s = SCHEMAS["mb_delete_house"]
    assert s.required == ("houseid",)
    assert "delete_list" not in s.rename.values()


def test_group_delete_uses_delete_list():
    assert SCHEMAS["mb_delete_user_group"].rename == {"usersgroupid": "delete_list"}
