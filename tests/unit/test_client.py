import hashlib

import httpx
import pytest
import respx

from mb_mcp.client import MikbillClient
from mb_mcp.config import Settings
from mb_mcp.errors import MikbillError

BASE = "https://mikbill.example.test"
LOGIN_OK = {"success": True, "data": {"jwt": "x"}, "error": 0}
UNAUTH = {"success": False, "error": 1403, "errortext": "UnAuth"}


@pytest.fixture
def settings():
    return Settings(base_url=BASE, login="admin", password="s3cret", scopes=frozenset({"read"}))


@pytest.fixture
async def client(settings):
    c = MikbillClient(settings)
    yield c
    await c.aclose()


@respx.mock
async def test_login_hashes_password_with_md5(client):
    auth = respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    respx.post(f"{BASE}/json/users/getcountfl").mock(return_value=httpx.Response(200, json={"success": True, "data": [1]}))
    await client.call("/json/users/getcountfl")
    body = auth.calls.last.request.content.decode()
    assert f"password={hashlib.md5(b's3cret').hexdigest()}" in body
    assert "s3cret" not in body
    assert "login=admin" in body


@respx.mock
async def test_login_is_lazy_and_done_once(client):
    auth = respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    respx.post(f"{BASE}/json/users/getcountfl").mock(return_value=httpx.Response(200, json={"success": True, "data": []}))
    await client.call("/json/users/getcountfl")
    await client.call("/json/users/getcountfl")
    assert auth.call_count == 1


@respx.mock
async def test_failed_login_raises_auth_failed(client):
    respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json={"success": False, "error": 1}))
    with pytest.raises(MikbillError) as e:
        await client.call("/json/users/getcountfl")
    assert e.value.code == "auth_failed"


@respx.mock
async def test_relogin_once_on_unauth(client):
    auth = respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    route = respx.post(f"{BASE}/json/users/getcountfl").mock(side_effect=[
        httpx.Response(200, json={"success": True, "data": [1]}),
        httpx.Response(200, json=UNAUTH),
        httpx.Response(200, json={"success": True, "data": [2]}),
    ])
    await client.call("/json/users/getcountfl")
    env = await client.call("/json/users/getcountfl")
    assert env["data"] == [2]
    assert route.call_count == 3
    assert auth.call_count == 2


@respx.mock
async def test_second_unauth_is_an_error(client):
    respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    respx.post(f"{BASE}/json/users/getcountfl").mock(return_value=httpx.Response(200, json=UNAUTH))
    with pytest.raises(MikbillError) as e:
        await client.call("/json/users/getcountfl")
    assert e.value.code == "auth_failed"


@respx.mock
async def test_http_200_with_success_false_is_an_error(client):
    respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    respx.post(f"{BASE}/json/options/delhouseflex").mock(return_value=httpx.Response(200, json={"success": False, "error": 8}))
    with pytest.raises(MikbillError) as e:
        await client.call("/json/options/delhouseflex", {"houseid": "1"})
    assert e.value.code == "conflict"


@respx.mock
async def test_non_200_and_non_json(client):
    respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    respx.post(f"{BASE}/a").mock(return_value=httpx.Response(500))
    respx.post(f"{BASE}/b").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(MikbillError) as e1:
        await client.call("/a")
    with pytest.raises(MikbillError) as e2:
        await client.call("/b")
    assert e1.value.code == "upstream_error" and e1.value.status == 500
    assert e2.value.code == "upstream_error"


@respx.mock
async def test_timeout_maps_to_timeout(client):
    respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    respx.post(f"{BASE}/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(MikbillError) as e:
        await client.call("/slow")
    assert e.value.code == "timeout"


@respx.mock
async def test_allow_empty_treats_empty_object_as_no_rows(client):
    respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    respx.post(f"{BASE}/json/users/getuserdevicesbyuid").mock(return_value=httpx.Response(200, json={}))
    env = await client.call("/json/users/getuserdevicesbyuid", {"uid": "1"}, allow_empty=True)
    assert env == {"success": True, "data": []}
    with pytest.raises(MikbillError):
        await client.call("/json/users/getuserdevicesbyuid", {"uid": "1"})


@respx.mock
async def test_params_are_form_encoded(client):
    respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))
    route = respx.post(f"{BASE}/json/users/userkickfl").mock(return_value=httpx.Response(200, json={"success": True}))
    await client.call("/json/users/userkickfl", {"uid": "854"})
    assert route.calls.last.request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert route.calls.last.request.content == b"uid=854"
