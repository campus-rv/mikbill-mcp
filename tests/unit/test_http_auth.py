import json
from dataclasses import replace

import pytest

from mb_mcp.config import HttpToken, Settings
from mb_mcp.http_auth import BearerAuthMiddleware, hash_token, request_principal, request_scopes

TOKEN = "s3cr3t-token"


@pytest.fixture
def settings():
    return Settings(
        base_url="https://x.test", login="a", password="b",
        scopes=frozenset({"read", "write", "finance"}),
        http_tokens=(HttpToken("reader", hash_token(TOKEN), frozenset({"read", "finance", "admin"})),),
        allowed_origins=frozenset({"https://ok.test"}),
    )


class Recorder:
    def __init__(self):
        self.sent = []
        self.inner_called = False
        self.seen_scopes = None
        self.seen_principal = None

    async def app(self, scope, receive, send):
        self.inner_called = True
        self.seen_scopes = request_scopes.get()
        self.seen_principal = request_principal.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(self, message):
        self.sent.append(message)

    async def receive(self):
        return {"type": "http.request", "body": b""}

    @property
    def status(self):
        return self.sent[0]["status"]


async def call(settings, headers):
    rec = Recorder()
    mw = BearerAuthMiddleware(rec.app, settings)
    scope = {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    await mw(scope, rec.receive, rec.send)
    return rec


async def test_missing_token_is_401(settings):
    rec = await call(settings, {})
    assert rec.status == 401 and not rec.inner_called
    assert (b"www-authenticate", b"Bearer") in rec.sent[0]["headers"]


async def test_wrong_token_is_401(settings):
    rec = await call(settings, {"authorization": "Bearer nope"})
    assert rec.status == 401 and not rec.inner_called


async def test_wrong_scheme_is_401(settings):
    rec = await call(settings, {"authorization": f"Basic {TOKEN}"})
    assert rec.status == 401


async def test_valid_token_sets_scopes_to_intersection_with_server_scopes(settings):
    rec = await call(settings, {"authorization": f"Bearer {TOKEN}"})
    assert rec.status == 200 and rec.inner_called
    assert rec.seen_scopes == frozenset({"read", "finance"})  # admin is not in the server scopes
    assert rec.seen_principal == "reader"
    # context is reset afterwards
    assert request_scopes.get() is None and request_principal.get() == "stdio"


async def test_unknown_origin_is_403_even_with_a_valid_token(settings):
    rec = await call(settings, {"authorization": f"Bearer {TOKEN}", "origin": "https://evil.test"})
    assert rec.status == 403 and not rec.inner_called


async def test_allowed_origin_passes(settings):
    rec = await call(settings, {"authorization": f"Bearer {TOKEN}", "origin": "https://ok.test"})
    assert rec.status == 200


async def test_origin_header_is_rejected_when_no_origins_are_configured(settings):
    rec = await call(replace(settings, allowed_origins=frozenset()),
                     {"authorization": f"Bearer {TOKEN}", "origin": "https://ok.test"})
    assert rec.status == 403


async def test_non_http_scopes_pass_through(settings):
    rec = Recorder()
    mw = BearerAuthMiddleware(rec.app, settings)
    await mw({"type": "lifespan"}, rec.receive, rec.send)
    assert rec.inner_called


async def test_error_body_is_json(settings):
    rec = await call(settings, {})
    assert json.loads(rec.sent[1]["body"]) == {"error": "missing or invalid bearer token"}
