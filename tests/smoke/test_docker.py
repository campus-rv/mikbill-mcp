"""Smoke test for the Docker image: build it, run it, talk MCP to it over HTTP and stdio.

Opt-in, because it needs a local Docker daemon:

    MB_SMOKE=1 python -m pytest tests/smoke -v

It builds the image as `mb-mcp:smoke` (layer cache makes repeat runs fast). To test an image you built
yourself, set MB_SMOKE_IMAGE=<name> and the build is skipped.

No real Mikbill is involved. The container talks to a tiny fake Mikbill started on the host
(reached as host.docker.internal), so the whole path is exercised: HTTP request, bearer token and scope
checks, Mikbill login (MD5 password), the upstream call and the shaped result.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "smoke-secret"
TOKEN_RO = "smoke-read-token"
TOKEN_FIN = "smoke-finance-token"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
PRIORITIES = [{"prioritytypeid": "1", "prioritytypename": "high"}, {"prioritytypeid": "2", "prioritytypename": "normal"}]

pytestmark = pytest.mark.skipif(os.environ.get("MB_SMOKE") != "1", reason="set MB_SMOKE=1 to run the Docker smoke test")


def docker(*args: str, check: bool = True, timeout: float = 600, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=check, input=stdin)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeMikbill:
    """Answers the login and one read endpoint, and records what it was asked."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                outer.requests.append((self.path, form))
                if self.path == "/extjs/index/auth":
                    body = {"success": True, "error": 0}
                elif self.path == "/json/ticketview/getprioritiestypeslist":
                    body = {"success": True, "totalCount": len(PRIORITIES), "data": PRIORITIES}
                else:
                    body = {"success": False, "error": 1, "errortext": "unexpected " + self.path}
                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args) -> None:
                pass

        self.port = free_port()
        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeMikbill":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="session")
def image() -> str:
    if docker("version", check=False).returncode != 0:
        pytest.skip("docker daemon not available")
    name = os.environ.get("MB_SMOKE_IMAGE")
    if name:
        return name
    name = "mb-mcp:smoke"
    built = docker("build", "-t", name, str(ROOT), check=False)
    assert built.returncode == 0, built.stderr[-2000:]
    return name


@pytest.fixture(scope="session")
def upstream():
    with FakeMikbill() as fake:
        yield fake


def container_env(upstream: FakeMikbill, **extra: str) -> list[str]:
    env = {"MB_BASE_URL": f"http://host.docker.internal:{upstream.port}", "MB_LOGIN": "smoke", "MB_PASSWORD": PASSWORD,
           **extra}
    flags: list[str] = ["--add-host", "host.docker.internal:host-gateway"]
    for key, value in env.items():
        flags += ["-e", f"{key}={value}"]
    return flags


# ---- the image itself ----------------------------------------------------------------------------

def test_runs_as_a_non_root_user(image):
    out = docker("run", "--rm", "--entrypoint", "id", image, "-u").stdout.strip()
    assert out != "0"


def test_lists_all_endpoints_offline(image):
    out = docker("run", "--rm", "--network", "none", image, "--list-endpoints").stdout
    assert "94 tools." in out and "mb_create_payment" in out


def test_missing_configuration_exits_2_with_a_clear_message(image):
    r = docker("run", "--rm", image, check=False)
    assert r.returncode == 2 and "MB_BASE_URL is required" in r.stderr


def test_http_needs_a_token_configured(image, upstream):
    r = docker("run", "--rm", *container_env(upstream), image, check=False)
    assert r.returncode == 2 and "MB_HTTP_TOKENS or MCP_HTTP_BEARER_TOKEN" in r.stderr


def test_image_has_no_curl_or_wget(image):
    r = docker("run", "--rm", "--entrypoint", "sh", image, "-c", "command -v curl wget", check=False)
    assert r.returncode != 0 and r.stdout.strip() == ""


def test_image_defaults_to_host_0_0_0_0_and_port_8000(image):
    env = json.loads(docker("image", "inspect", "-f", "{{json .Config.Env}}", image).stdout)
    assert "MCP_HTTP_HOST=0.0.0.0" in env and "MCP_HTTP_PORT=8000" in env and "MCP_TRANSPORT=http" in env
    assert "8000/tcp" in json.loads(docker("image", "inspect", "-f", "{{json .Config.ExposedPorts}}", image).stdout)


# ---- Streamable HTTP ------------------------------------------------------------------------------

@contextlib.contextmanager
def running(image: str, upstream: FakeMikbill, container_port: int = 8000, docker_args: tuple[str, ...] = (),
            **env: str):
    """Starts the image detached with its port published on a free local port; yields (base url, container id).

    Waits for the Docker health check (which calls /health) to report healthy.
    """
    port = free_port()
    cid = docker("run", "-d", "--rm", "-p", f"127.0.0.1:{port}:{container_port}", *docker_args, *container_env(
        upstream, MB_SCOPES="read,write,finance", MCP_ALLOWED_HOSTS=f"127.0.0.1:{port}", **env), image).stdout.strip()
    try:
        deadline = time.time() + 45
        status = ""
        while time.time() < deadline:
            status = docker("inspect", "-f", "{{.State.Health.Status}}", cid, check=False).stdout.strip()
            if status == "healthy":
                break
            time.sleep(1)
        assert status == "healthy", f"container health is {status!r}\n{docker('logs', cid, check=False).stderr[-1500:]}"
        yield f"http://127.0.0.1:{port}", cid
    finally:
        docker("rm", "-f", cid, check=False)


@pytest.fixture(scope="module")
def http_container(image, upstream):
    tokens = json.dumps([
        {"id": "ro", "sha256": sha256(TOKEN_RO), "scopes": ["read"]},
        {"id": "fin", "sha256": sha256(TOKEN_FIN), "scopes": ["read", "finance"]},
    ])
    with running(image, upstream, MB_HTTP_TOKENS=tokens) as (base, cid):
        yield base, cid


@pytest.fixture(scope="module")
def http_server(http_container):
    return http_container[0] + "/mcp"


def rpc(url: str, token: str | None, method: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
    h = {**HEADERS, **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return httpx.post(url, headers=h, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, timeout=20)


def parse(resp: httpx.Response) -> dict:
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in resp.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


def test_http_rejects_a_missing_or_wrong_token(http_server):
    assert rpc(http_server, None, "tools/list").status_code == 401
    assert rpc(http_server, "wrong", "tools/list").status_code == 401


def test_http_rejects_an_unknown_host_header(http_server):
    assert rpc(http_server, TOKEN_RO, "tools/list", headers={"Host": "evil.example:8000"}).status_code == 421


def test_http_tools_list_follows_the_token_scopes(http_server):
    ro = parse(rpc(http_server, TOKEN_RO, "tools/list"))["result"]["tools"]
    fin = parse(rpc(http_server, TOKEN_FIN, "tools/list"))["result"]["tools"]
    assert len(ro) == 25 and len(fin) == 25 + 13
    assert "mb_create_payment" in {t["name"] for t in fin} and "mb_create_payment" not in {t["name"] for t in ro}


def test_http_read_call_goes_through_login_to_the_upstream(http_server, upstream):
    upstream.requests.clear()
    r = rpc(http_server, TOKEN_RO, "tools/call", {"name": "mb_list_ticket_priorities", "arguments": {}})
    result = parse(r)["result"]
    assert result["isError"] is False
    body = json.loads(result["content"][0]["text"])
    assert body["ok"] is True and body["data"] == PRIORITIES
    paths = [p for p, _ in upstream.requests]
    assert paths == ["/extjs/index/auth", "/json/ticketview/getprioritiestypeslist"]
    login = upstream.requests[0][1]
    assert login == {"login": "smoke", "password": hashlib.md5(PASSWORD.encode()).hexdigest()}  # noqa: S324 (protocol)


def test_http_gated_tool_is_denied_without_its_scope_and_never_reaches_the_upstream(http_server, upstream):
    upstream.requests.clear()
    args = {"uid": 1, "bughtypeid": 17, "deposit": 1, "prim2": "x", "reason": "smoke test"}
    result = parse(rpc(http_server, TOKEN_RO, "tools/call", {"name": "mb_create_payment", "arguments": args}))["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"]["code"] == "scope_denied"
    assert not [p for p, _ in upstream.requests if p != "/extjs/index/auth"]


def test_http_gated_tool_with_scope_returns_a_dry_run_and_does_not_write(http_server, upstream):
    upstream.requests.clear()
    args = {"uid": 1, "bughtypeid": 17, "deposit": 1, "prim2": "x", "reason": "smoke test"}
    result = parse(rpc(http_server, TOKEN_FIN, "tools/call", {"name": "mb_create_payment", "arguments": args}))["result"]
    body = json.loads(result["content"][0]["text"])
    assert body["dry_run"] is True and body["data"]["approval_token"]
    assert "/json/users/paymentdoflex" not in [p for p, _ in upstream.requests]


def test_http_does_not_leak_the_password_into_the_logs(http_container):
    # the log of the running container is the one place a secret could end up by accident
    logs = docker("logs", http_container[1], check=False)
    assert PASSWORD not in logs.stdout + logs.stderr and TOKEN_RO not in logs.stdout + logs.stderr


def test_health_endpoint_is_open_and_reports_ok(http_container):
    r = httpx.get(http_container[0] + "/health", timeout=10)  # no Authorization header
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert httpx.post(http_container[0] + "/health", timeout=10).status_code == 401


def test_docker_health_check_uses_python_on_the_health_endpoint(http_container):
    health = " ".join(json.loads(docker("inspect", "-f", "{{json .Config.Healthcheck.Test}}", http_container[1]).stdout))
    assert "python" in health and "/health" in health and "curl" not in health
    log = json.loads(docker("inspect", "-f", "{{json .State.Health.Log}}", http_container[1]).stdout)
    assert log and log[-1]["ExitCode"] == 0


# ---- MCP_HTTP_BEARER_TOKEN and MCP_HTTP_PORT --------------------------------------------------------

BEARER = "smoke-bearer-token-0123456789"


def test_bearer_token_env_authenticates_with_the_server_scopes(image, upstream):
    with running(image, upstream, MCP_HTTP_BEARER_TOKEN=BEARER) as (base, cid):
        url = base + "/mcp"
        assert rpc(url, None, "tools/list").status_code == 401
        assert rpc(url, "wrong-token-wrong-token", "tools/list").status_code == 401
        tools = parse(rpc(url, BEARER, "tools/list"))["result"]["tools"]
        assert len(tools) == 25 + 9 + 13  # read, write and finance, as set in MB_SCOPES
        call = parse(rpc(url, BEARER, "tools/call", {"name": "mb_list_ticket_priorities", "arguments": {}}))["result"]
        assert call["isError"] is False
        logs = docker("logs", cid, check=False)
        assert BEARER not in logs.stdout + logs.stderr


def test_audit_log_goes_to_a_volume_under_the_compose_hardening(image, upstream):
    """The flags of examples/docker-compose.yml: read-only root, no capabilities, no-new-privileges, /data volume."""
    volume = f"mbmcp-smoke-{os.getpid()}"
    docker("volume", "create", volume)
    hardening = ("-v", f"{volume}:/data", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true")
    try:
        with running(image, upstream, docker_args=hardening, MCP_HTTP_BEARER_TOKEN=BEARER,
                     MB_AUDIT_FILE="/data/audit.jsonl") as (base, _):
            # account_state is not in this server's scopes, so the call is denied and audited
            r = parse(rpc(base + "/mcp", BEARER, "tools/call", {"name": "mb_delete_user", "arguments": {
                "uid": 1, "reason": "smoke test"}}))["result"]
            assert json.loads(r["content"][0]["text"])["error"]["code"] == "scope_denied"
        out = docker("run", "--rm", "-v", f"{volume}:/data", "--entrypoint", "cat", image, "/data/audit.jsonl").stdout
        events = [json.loads(line) for line in out.splitlines()]
        assert [(e["tool"], e["outcome"], e["principal"]) for e in events] == [("mb_delete_user", "scope_denied", "bearer")]
    finally:
        docker("volume", "rm", "-f", volume, check=False)


def test_a_short_bearer_token_stops_the_container(image, upstream):
    r = docker("run", "--rm", *container_env(upstream, MCP_HTTP_BEARER_TOKEN="short"), image, check=False)
    assert r.returncode == 2 and "MCP_HTTP_BEARER_TOKEN" in r.stderr


def test_port_can_be_changed_and_the_health_check_follows(image, upstream):
    with running(image, upstream, container_port=9000, MCP_HTTP_PORT="9000", MCP_HTTP_BEARER_TOKEN=BEARER) as (base, _):
        assert httpx.get(base + "/health", timeout=10).json() == {"status": "ok"}
        assert rpc(base + "/mcp", BEARER, "tools/list").status_code == 200


# ---- stdio ----------------------------------------------------------------------------------------

# The same image in local mode. The HTTP-only settings of a remote deployment (a bearer token that is too
# short to be valid, a bad port) are ignored, so one env file can serve both modes.
STDIO_ENV = {"MCP_TRANSPORT": "stdio", "MCP_HTTP_BEARER_TOKEN": "short", "MCP_HTTP_PORT": "nope"}


def test_stdio_mode_container_is_healthy_without_a_server(image, upstream):
    cid = docker("run", "-d", "-i", "--rm", *container_env(upstream, **STDIO_ENV), image).stdout.strip()
    try:
        deadline = time.time() + 45
        status = ""
        while time.time() < deadline:
            status = docker("inspect", "-f", "{{.State.Health.Status}}", cid, check=False).stdout.strip()
            if status == "healthy":
                break
            time.sleep(1)
        assert status == "healthy", docker("logs", cid, check=False).stderr[-1500:]
        assert docker("port", cid, check=False).stdout.strip() == ""  # nothing is published or listening for clients
    finally:
        docker("rm", "-f", cid, check=False)


def test_stdio_initialize_and_list_tools(image, upstream):
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mb_list_ticket_priorities", "arguments": {}}},
    ]
    # keep stdin open until every reply has arrived, as a real client does (EOF closes the server)
    proc = subprocess.Popen(["docker", "run", "-i", "--rm", *container_env(upstream, **STDIO_ENV), image],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    replies: dict[int, dict] = {}
    try:
        for m in messages:
            proc.stdin.write(json.dumps(m) + "\n")
        proc.stdin.flush()
        deadline = time.time() + 60
        while len(replies) < 3 and time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)  # anything else on stdout would break the protocol
            if "id" in msg:
                replies[msg["id"]] = msg
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    assert set(replies) == {1, 2, 3}, replies
    assert replies[1]["result"]["serverInfo"]["name"]
    assert len(replies[2]["result"]["tools"]) == 25 + 9  # default scopes: read and write
    call = replies[3]["result"]
    assert call["isError"] is False and json.loads(call["content"][0]["text"])["data"] == PRIORITIES
    assert proc.returncode == 0  # closing stdin is a clean shutdown
