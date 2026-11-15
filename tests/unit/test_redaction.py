from mb_mcp.redaction import LOG_KEYS, MASK, redact


def test_output_credentials_are_masked_recursively():
    data = {"data": {"user": "u", "password": "secret", "router_pass": "r", "nested": [{"pass": "p"}]}}
    out = redact(data)
    assert out["data"]["password"] == MASK
    assert out["data"]["router_pass"] == MASK
    assert out["data"]["nested"][0]["pass"] == MASK
    assert out["data"]["user"] == "u"


def test_empty_values_stay_empty():
    assert redact({"password": ""})["password"] == ""
    assert redact({"password": None})["password"] is None


def test_original_is_not_modified():
    data = {"password": "x"}
    redact(data)
    assert data["password"] == "x"


def test_personal_fields_are_only_masked_for_logs():
    data = {"phone": "123", "email": "a@b.c", "fio": "Name"}
    assert redact(data) == data
    logged = redact(data, LOG_KEYS)
    assert logged["phone"] == MASK and logged["email"] == MASK and logged["fio"] == MASK


def test_keys_are_case_insensitive():
    assert redact({"Password": "x"})["Password"] == MASK
