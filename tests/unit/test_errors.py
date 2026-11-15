import pytest

from mb_mcp.errors import MikbillError, error_from_envelope, is_unauth


def err(env):
    return error_from_envelope(env)


def test_success_is_none():
    assert err({"success": True, "data": []}) is None


@pytest.mark.parametrize("env,code", [
    ({"success": False, "error": 1403, "errortext": "UnAuth"}, "auth_failed"),
    ({"success": False, "error": "1", "errortext": "Denied by settings"}, "denied_by_settings"),
    ({"success": False, "error": 2, "errortext": "MSG_ERROR_ABONENT_DUPLICATE_LOGIN"}, "precondition_failed"),
    ({"success": False, "error": 27, "errortext": "service_portal for subscription=115 not found"}, "precondition_failed"),
    ({"success": False, "error": 1, "errortext": "Не указан обязательный параметр: nasid", "errorKey": "nasid"}, "validation"),
    ({"success": False, "error": 8}, "conflict"),
    ({"success": False, "error": 1202, "errortext": "Что то пошло не так"}, "upstream_error"),
    ({"success": False, "error": 99}, "upstream_error"),
    ({}, "upstream_error"),
    ({"data": []}, "upstream_error"),
])
def test_mapping(env, code):
    e = err(env)
    assert isinstance(e, MikbillError)
    assert e.code == code


def test_validation_message_names_the_key():
    e = err({"success": False, "error": 1, "errortext": "Не указан обязательный параметр: nasid", "errorKey": "nasid"})
    assert "nasid" in e.message


def test_is_unauth():
    assert is_unauth({"success": False, "error": 1403})
    assert not is_unauth({"success": True})
    assert not is_unauth({"success": False, "error": 8})


def test_unknown_code_rejected():
    with pytest.raises(ValueError):
        MikbillError("nope", "x")
