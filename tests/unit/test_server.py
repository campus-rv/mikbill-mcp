"""End-to-end through the real MCP SDK: in-memory client session against build_server()."""

import json

import pytest
from mcp.client import Client

from conftest import ALL_SCOPE_SET, FakeClient
from mb_mcp.config import ALL_SCOPES, Settings
from mb_mcp.gating import ApprovalStore
from mb_mcp.http_auth import request_scopes
from mb_mcp.runtime import Runtime
from mb_mcp.server import build_server


def make(scopes, fake=None):
    settings = Settings(base_url="https://x.test", login="a", password="b", scopes=frozenset(scopes), max_bulk=5)
    fake = fake or FakeClient()
    return Runtime(settings, fake, ApprovalStore()), fake


def payload(result):
    return json.loads(result.content[0].text)


async def test_list_tools_by_scope():
    rt, _ = make({"read", "write"})
    async with Client(build_server(rt)) as client:
        tools = (await client.list_tools()).tools
    assert len(tools) == 34
    names = {t.name for t in tools}
    assert "mb_kick_user" in names and "mb_delete_user" not in names and "mb_add_house" not in names


async def test_all_scopes_list_94_tools_with_schemas():
    rt, _ = make(ALL_SCOPES)
    async with Client(build_server(rt)) as client:
        tools = (await client.list_tools()).tools
    assert len(tools) == 94
    by_name = {t.name: t for t in tools}
    gated = by_name["mb_create_payment"].input_schema
    assert "dry_run" in gated["properties"] and "reason" in gated["required"]
    assert "dry_run" not in by_name["mb_get_user"].input_schema["properties"]


async def test_call_read_tool_returns_json_text():
    rt, fake = make({"read"}, FakeClient({"/json/users/getuserdatambp": {"success": True, "data": {"user": "u", "password": "p"}}}))
    async with Client(build_server(rt)) as client:
        result = await client.call_tool("mb_get_user", {"uid": 1})
    assert not result.is_error
    body = payload(result)
    assert body["ok"] is True and body["data"]["user"] == "u" and body["data"]["password"] != "p"
    assert fake.posted("/json/users/getuserdatambp") == [{"uid": "1"}]


async def test_errors_set_is_error():
    rt, _ = make({"read"})
    async with Client(build_server(rt)) as client:
        bad = await client.call_tool("mb_get_user", {})
        denied = await client.call_tool("mb_bulk_delete_users", {"uids": [1], "reason": "test"})
    assert bad.is_error and payload(bad)["error"]["code"] == "validation"
    assert denied.is_error and payload(denied)["error"]["code"] == "scope_denied"


async def test_gated_two_step_flow_through_the_sdk():
    rt, fake = make(ALL_SCOPES)
    args = {"uid": 1, "bughtypeid": 17, "deposit": 10, "prim2": "note", "reason": "sdk test"}
    async with Client(build_server(rt)) as client:
        preview = payload(await client.call_tool("mb_create_payment", args))
        assert preview["dry_run"] is True and fake.posted("/json/users/paymentdoflex") == []
        done = await client.call_tool("mb_create_payment", {**args, "dry_run": False,
                                                           "approval_token": preview["data"]["approval_token"]})
    assert not done.is_error
    assert len(fake.posted("/json/users/paymentdoflex")) == 1


async def test_request_scopes_context_limits_listing():
    rt, _ = make(ALL_SCOPES)
    token = request_scopes.set(frozenset({"read"}))
    try:
        async with Client(build_server(rt)) as client:
            tools = (await client.list_tools()).tools
            denied = await client.call_tool("mb_kick_user", {"uid": 1})
    finally:
        request_scopes.reset(token)
    assert len(tools) == 25
    assert denied.is_error and payload(denied)["error"]["code"] == "scope_denied"
