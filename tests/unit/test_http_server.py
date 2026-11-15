"""Real HTTP round trip: uvicorn in a thread, bearer auth, stateless Streamable HTTP, per-token scopes."""

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from conftest import FakeClient
from mb_mcp.config import HttpToken, Settings
from mb_mcp.http_auth import hash_token
from mb_mcp.runtime import Runtime
from mb_mcp.gating import ApprovalStore
from mb_mcp.server import build_http_app

TOKEN_RO = "read-only-token"
TOKEN_FIN = "finance-token"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    port = free_port()
    settings = Settings(
        base_url="https://x.test", login="a", password="b",
        scopes=frozenset({"read", "write", "finance"}),
        http_tokens=(HttpToken("ro", hash_token(TOKEN_RO), frozenset({"read"})),
                     HttpToken("fin", hash_token(TOKEN_FIN), frozenset({"read", "finance"}))),
    )
    runtime = Runtime(settings, FakeClient(), ApprovalStore())
    app = build_http_app(runtime, "127.0.0.1", port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def rpc(url, token, method, params=None, rid=1):
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    return httpx.post(url, headers=headers, json=body, timeout=10)


def parse(resp):
    """The response is JSON or a single SSE event."""
    text = resp.text
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


def test_no_token_is_401(server_url):
    assert rpc(server_url, None, "tools/list").status_code == 401


def test_wrong_token_is_401(server_url):
    assert rpc(server_url, "nope", "tools/list").status_code == 401


def test_origin_header_is_403(server_url):
    r = httpx.post(server_url, headers={**HEADERS, "Authorization": f"Bearer {TOKEN_RO}", "Origin": "https://evil.test"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 403


def test_read_only_token_sees_only_read_tools(server_url):
    r = rpc(server_url, TOKEN_RO, "tools/list")
    assert r.status_code == 200, r.text
    tools = parse(r)["result"]["tools"]
    assert len(tools) == 25
    assert all(not t["name"].startswith(("mb_create", "mb_delete", "mb_bulk")) for t in tools)


def test_finance_token_sees_read_and_finance_tools(server_url):
    tools = parse(rpc(server_url, TOKEN_FIN, "tools/list"))["result"]["tools"]
    names = {t["name"] for t in tools}
    assert len(tools) == 25 + 13
    assert "mb_create_payment" in names and "mb_kick_user" not in names  # write is not in this token's scopes


def test_scope_denied_tool_call_is_an_error_result(server_url):
    r = rpc(server_url, TOKEN_RO, "tools/call", {"name": "mb_create_payment", "arguments": {
        "uid": 1, "bughtypeid": 17, "deposit": 1, "prim2": "x", "reason": "http test"}})
    assert r.status_code == 200
    result = parse(r)["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"]["code"] == "scope_denied"
