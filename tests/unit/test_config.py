import pytest

from mb_mcp.config import ConfigError, Settings, parse_scopes, parse_tokens
from mb_mcp.http_auth import hash_token


def env(**kw):
    base = {"MB_BASE_URL": "https://x.test/", "MB_LOGIN": "a", "MB_PASSWORD": "b"}
    base.update(kw)
    return base


def test_defaults_are_read_and_write_only():
    s = Settings.from_env(env())
    assert s.scopes == frozenset({"read", "write"})
    assert s.base_url == "https://x.test"
    assert s.max_bulk == 50
    assert not s.insecure_tls


def test_required_values():
    with pytest.raises(ConfigError):
        Settings.from_env({"MB_BASE_URL": "https://x.test"})


def test_unknown_scope_rejected():
    with pytest.raises(ConfigError):
        parse_scopes("read,bogus")


def test_insecure_tls_needs_explicit_one():
    assert Settings.from_env(env(MB_INSECURE_TLS="1")).insecure_tls
    assert not Settings.from_env(env(MB_INSECURE_TLS="true")).insecure_tls


def test_tokens_parse():
    raw = '[{"id": "a", "sha256": "%s", "scopes": ["read"]}]' % hash_token("tok")
    (t,) = parse_tokens(raw)
    assert t.id == "a" and t.scopes == frozenset({"read"})


def test_tokens_invalid():
    with pytest.raises(ConfigError):
        parse_tokens("not json")
    with pytest.raises(ConfigError):
        parse_tokens('[{"id": "a"}]')
