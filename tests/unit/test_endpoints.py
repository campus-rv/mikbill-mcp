from collections import Counter

from mb_mcp.config import GATED_SCOPES
from mb_mcp.endpoints import ALL, EXCLUDED, PENDING
from mb_mcp.schemas import SCHEMAS


def test_tool_counts_per_scope():
    counts = Counter(ep.scope for ep in ALL)
    assert counts == {"read": 19, "write": 20, "account_state": 22, "finance": 13, "admin": 6}
    assert len(ALL) == 80


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


def test_every_delete_is_in_a_gated_scope():
    for ep in ALL:
        if "delete" in ep.tool:
            assert ep.gated, ep.tool


def test_delete_house_never_uses_delete_list():
    s = SCHEMAS["mb_delete_house"]
    assert s.required == ("houseid",)
    assert "delete_list" not in s.rename.values()


def test_group_delete_uses_delete_list():
    assert SCHEMAS["mb_delete_user_group"].rename == {"usersgroupid": "delete_list"}
