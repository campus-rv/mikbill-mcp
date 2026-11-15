import pytest

from mb_mcp import __main__ as cli

BASE = {"MB_BASE_URL": "https://x.test", "MB_LOGIN": "a", "MB_PASSWORD": "b"}


@pytest.fixture
def served(monkeypatch):
    """Replaces the HTTP server with a recorder and clears the process environment of MB_/MCP_ settings."""
    import os
    for key in [k for k in os.environ if k.startswith(("MB_", "MCP_"))]:
        monkeypatch.delenv(key)
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    calls = []

    async def fake_run_http(settings, host, port):
        calls.append((host, port))

    async def fake_run_stdio(settings):
        calls.append("stdio")

    monkeypatch.setattr("mb_mcp.server.run_http", fake_run_http)
    monkeypatch.setattr("mb_mcp.server.run_stdio", fake_run_stdio)
    return calls


def test_http_defaults_to_localhost_8765(served):
    assert cli.main(["--http"]) == 0
    assert served == [("127.0.0.1", 8765)]


def test_http_takes_host_and_port_from_the_environment(served, monkeypatch):
    monkeypatch.setenv("MCP_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_HTTP_PORT", "8000")
    assert cli.main(["--http"]) == 0
    assert served == [("0.0.0.0", 8000)]


def test_command_line_options_override_the_environment(served, monkeypatch):
    monkeypatch.setenv("MCP_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_HTTP_PORT", "8000")
    assert cli.main(["--http", "--host", "127.0.0.1", "--port", "9100"]) == 0
    assert served == [("127.0.0.1", 9100)]


def test_a_bad_port_in_the_environment_is_a_configuration_error(served, monkeypatch, capsys):
    monkeypatch.setenv("MCP_HTTP_PORT", "nope")
    assert cli.main(["--http"]) == 2
    assert "MCP_HTTP_PORT" in capsys.readouterr().err


def test_stdio_is_the_default_transport(served):
    assert cli.main([]) == 0
    assert served == ["stdio"]


def test_mcp_transport_selects_http_without_a_flag(served, monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_HTTP_PORT", "8000")
    assert cli.main([]) == 0
    assert served == [("127.0.0.1", 8000)]


def test_transport_option_overrides_mcp_transport(served, monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    assert cli.main(["--transport", "stdio"]) == 0
    assert served == ["stdio"]


def test_stdio_ignores_a_bad_http_setting_so_one_env_file_serves_both_modes(served, monkeypatch):
    monkeypatch.setenv("MCP_HTTP_BEARER_TOKEN", "short")
    monkeypatch.setenv("MCP_HTTP_PORT", "nope")
    assert cli.main(["--transport", "stdio"]) == 0
    assert served == ["stdio"]


def test_an_unknown_mcp_transport_is_a_configuration_error(served, monkeypatch, capsys):
    monkeypatch.setenv("MCP_TRANSPORT", "carrier-pigeon")
    assert cli.main([]) == 2
    assert "MCP_TRANSPORT" in capsys.readouterr().err
