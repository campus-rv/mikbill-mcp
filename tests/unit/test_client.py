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


# ---- L4: MB_CA_BUNDLE is loaded into an SSL context --------------------------------------------------------------

def test_a_ca_bundle_is_loaded_without_the_httpx_deprecation_warning(settings):
    import warnings
    from dataclasses import replace

    import certifi

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # httpx 0.28 warns when `verify` is a path
        c = MikbillClient(replace(settings, ca_bundle=certifi.where()))
    assert c._http is not None


@pytest.mark.parametrize("make_path", ["missing", "not_a_certificate"])
def test_an_unusable_ca_bundle_is_a_start_up_error_that_names_the_variable(settings, tmp_path, make_path):
    from dataclasses import replace

    from mb_mcp.config import ConfigError
    path = tmp_path / "ca.pem"
    if make_path == "not_a_certificate":
        path.write_text("this is not a certificate", encoding="utf-8")
    with pytest.raises(ConfigError, match="MB_CA_BUNDLE .* cannot be loaded"):
        MikbillClient(replace(settings, ca_bundle=str(path)))


def test_insecure_tls_still_turns_verification_off(settings):
    from dataclasses import replace
    c = MikbillClient(replace(settings, insecure_tls=True, ca_bundle="/ignored/when/insecure.pem"))
    assert c._http is not None


# ---- L5: one re-login for concurrent 1403s, and a cool-down after a rejected login --------------------------------

class Clock:
    """Stands in for the time module inside mb_mcp.client (patching time.monotonic itself would stop asyncio)."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    import mb_mcp.client as client_module
    c = Clock()
    monkeypatch.setattr(client_module, "time", c)
    return c


@respx.mock
async def test_concurrent_unauthenticated_calls_cause_one_login_not_one_each(client):
    import asyncio
    auth = respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json=LOGIN_OK))

    def data(_request):
        # the session expires after the first login; it is valid again after the second
        return httpx.Response(200, json=UNAUTH if auth.call_count < 2 else {"success": True, "data": [1]})

    respx.post(f"{BASE}/json/users/getcountfl").mock(side_effect=data)
    await client._ensure_login()
    assert auth.call_count == 1
    results = await asyncio.gather(*[client.call("/json/users/getcountfl") for _ in range(8)])
    assert all(r["success"] for r in results)
    assert auth.call_count == 2  # one re-login, not eight


@respx.mock
async def test_a_rejected_login_is_not_retried_by_every_call(client, clock):
    auth = respx.post(f"{BASE}/extjs/index/auth").mock(return_value=httpx.Response(200, json={"success": False, "error": 1}))
    respx.post(f"{BASE}/json/users/getcountfl").mock(return_value=httpx.Response(200, json={"success": True, "data": []}))
    with pytest.raises(MikbillError) as first:
        await client.call("/json/users/getcountfl")
    assert first.value.code == "auth_failed" and auth.call_count == 1
    for _ in range(5):  # inside the cool-down: fail at once, no request to Mikbill
        with pytest.raises(MikbillError) as again:
            await client.call("/json/users/getcountfl")
        assert again.value.code == "auth_failed" and "not trying again" in again.value.message
    assert auth.call_count == 1


@respx.mock
async def test_the_cool_down_ends_and_a_good_login_clears_it(client, clock):
    answers = [httpx.Response(200, json={"success": False, "error": 1}), httpx.Response(200, json=LOGIN_OK)]
    auth = respx.post(f"{BASE}/extjs/index/auth").mock(side_effect=answers)
    respx.post(f"{BASE}/json/users/getcountfl").mock(return_value=httpx.Response(200, json={"success": True, "data": []}))
    with pytest.raises(MikbillError):
        await client.call("/json/users/getcountfl")
    clock.now += 29
    with pytest.raises(MikbillError, match="not trying again"):
        await client.call("/json/users/getcountfl")
    assert auth.call_count == 1
    clock.now += 2  # 31 s after the rejection
    assert (await client.call("/json/users/getcountfl"))["success"]
    assert auth.call_count == 2
    await client.call("/json/users/getcountfl")  # logged in: no further login, and nothing blocks
    assert auth.call_count == 2


@respx.mock
async def test_a_failed_re_login_after_1403_also_starts_the_cool_down(client, clock):
    answers = [httpx.Response(200, json=LOGIN_OK), httpx.Response(200, json={"success": False, "error": 1})]
    auth = respx.post(f"{BASE}/extjs/index/auth").mock(side_effect=answers)
    respx.post(f"{BASE}/json/users/getcountfl").mock(return_value=httpx.Response(200, json=UNAUTH))
    with pytest.raises(MikbillError) as e1:
        await client.call("/json/users/getcountfl")
    assert e1.value.code == "auth_failed" and auth.call_count == 2
    with pytest.raises(MikbillError, match="not trying again"):
        await client.call("/json/users/getcountfl")
    assert auth.call_count == 2


@respx.mock
async def test_a_timeout_during_login_does_not_start_the_cool_down(client, clock):
    answers = [httpx.ConnectTimeout("slow"), httpx.Response(200, json=LOGIN_OK)]
    auth = respx.post(f"{BASE}/extjs/index/auth").mock(side_effect=answers)
    respx.post(f"{BASE}/json/users/getcountfl").mock(return_value=httpx.Response(200, json={"success": True, "data": []}))
    with pytest.raises(MikbillError) as e1:
        await client.call("/json/users/getcountfl")
    assert e1.value.code == "timeout"
    assert (await client.call("/json/users/getcountfl"))["success"]  # tried again at once
    assert auth.call_count == 2
