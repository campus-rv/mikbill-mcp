import pytest

from mb_mcp.config import ConfigError, Settings, parse_scopes
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


HTTP = {"transport": "http"}


def test_transport_defaults_to_stdio_and_follows_mcp_transport_or_the_argument():
    assert Settings.from_env(env()).transport == "stdio"
    assert Settings.from_env(env(MCP_TRANSPORT="http")).transport == "http"
    assert Settings.from_env(env(MCP_TRANSPORT="HTTP")).transport == "http"
    assert Settings.from_env(env(MCP_TRANSPORT="stdio"), transport="http").transport == "http"  # argument wins
    with pytest.raises(ConfigError, match="MCP_TRANSPORT"):
        Settings.from_env(env(MCP_TRANSPORT="websocket"))


def test_allowed_hosts_and_origins_are_comma_lists():
    s = Settings.from_env(env(MCP_ALLOWED_HOSTS="mcp.example.com, localhost:9000,", MCP_ALLOWED_ORIGINS="https://a.test"),
                          **HTTP)
    assert s.allowed_hosts == frozenset({"mcp.example.com", "localhost:9000"})
    assert s.allowed_origins == frozenset({"https://a.test"})
    assert Settings.from_env(env(), **HTTP).allowed_hosts == frozenset()


def test_the_old_mb_allowed_names_are_not_read():
    s = Settings.from_env(env(MB_ALLOWED_HOSTS="old.example", MB_ALLOWED_ORIGINS="https://old.test"), **HTTP)
    assert s.allowed_hosts == frozenset() and s.allowed_origins == frozenset()


def test_http_host_and_port_default_to_localhost_8000_and_come_from_the_environment():
    s = Settings.from_env(env(), **HTTP)
    assert (s.http_host, s.http_port) == ("127.0.0.1", 8000)
    s = Settings.from_env(env(MCP_HTTP_HOST="0.0.0.0", MCP_HTTP_PORT="9000"), **HTTP)
    assert (s.http_host, s.http_port) == ("0.0.0.0", 9000)


@pytest.mark.parametrize("port", ["abc", "0", "70000", "-1"])
def test_http_port_must_be_a_valid_port(port):
    with pytest.raises(ConfigError):
        Settings.from_env(env(MCP_HTTP_PORT=port), **HTTP)


def test_bearer_token_env_adds_a_token_with_the_server_scopes_and_keeps_only_its_hash():
    secret = "a-long-enough-secret-token"
    s = Settings.from_env(env(MCP_HTTP_BEARER_TOKEN=secret, MB_SCOPES="read,finance"), **HTTP)
    (t,) = s.http_tokens
    assert t.id == "bearer" and t.scopes == frozenset({"read", "finance"})
    assert t.sha256 == hash_token(secret) and secret not in repr(s)


def test_a_short_bearer_token_is_refused_in_http_mode():
    with pytest.raises(ConfigError, match="at least 16"):
        Settings.from_env(env(MCP_HTTP_BEARER_TOKEN="short"), **HTTP)


def test_a_blank_bearer_token_is_ignored():
    assert Settings.from_env(env(MCP_HTTP_BEARER_TOKEN="  "), **HTTP).http_tokens == ()


def test_http_only_settings_are_ignored_and_not_validated_in_stdio_mode():
    s = Settings.from_env(env(
        MCP_HTTP_BEARER_TOKEN="short", MCP_HTTP_PORT="nope",
        MCP_HTTP_HOST="0.0.0.0", MCP_ALLOWED_HOSTS="a.example", MCP_ALLOWED_ORIGINS="https://a.test"))
    assert s.transport == "stdio"
    assert s.http_tokens == () and s.allowed_hosts == frozenset() and s.allowed_origins == frozenset()
    assert (s.http_host, s.http_port) == ("127.0.0.1", 8000)


# ---- L2: MB_MAX_BULK has bounds -------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["0", "-1", "-5", "inf", "nan", "1.5", "1001", "5000"])
def test_mb_max_bulk_must_be_a_whole_number_between_1_and_1000(value):
    with pytest.raises(ConfigError, match="MB_MAX_BULK must be a whole number, between 1 and 1000"):
        Settings.from_env(env(MB_MAX_BULK=value))


def test_mb_max_bulk_text_is_refused_as_not_a_number():
    with pytest.raises(ConfigError, match="MB_MAX_BULK must be a number"):
        Settings.from_env(env(MB_MAX_BULK="abc"))


@pytest.mark.parametrize("value,expected", [("1", 1), ("50", 50), ("1000", 1000), ("25.0", 25), ("", 50)])
def test_mb_max_bulk_accepts_values_in_range(value, expected):
    assert Settings.from_env(env(MB_MAX_BULK=value)).max_bulk == expected


# ---- one bearer token: MB_HTTP_TOKENS (several tokens with their own scopes) is gone ------------------------------

def test_mb_http_tokens_is_no_longer_read():
    raw = '[{"id": "a", "sha256": "%s", "scopes": ["read"]}]' % hash_token("tok")
    s = Settings.from_env(env(MB_HTTP_TOKENS=raw), **HTTP)
    assert s.http_tokens == ()  # nothing configured: serving HTTP then fails with "MCP_HTTP_BEARER_TOKEN is required"
    s = Settings.from_env(env(MB_HTTP_TOKENS=raw, MCP_HTTP_BEARER_TOKEN="a-long-enough-secret-token"), **HTTP)
    assert [t.id for t in s.http_tokens] == ["bearer"]


def test_the_one_token_gets_every_scope_in_mb_scopes():
    s = Settings.from_env(env(MCP_HTTP_BEARER_TOKEN="a-long-enough-secret-token", MB_SCOPES="read,write,finance"), **HTTP)
    (t,) = s.http_tokens
    assert t.scopes == frozenset({"read", "write", "finance"})
