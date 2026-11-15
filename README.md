# mb-mcp

MCP server for the Mikbill admin API. 80 tools in five scopes, with dry-run and approval for everything that changes billing, account state, privileges, or deletes data. The design is in [SPEC.md](SPEC.md); per-endpoint evidence from the public demo is in [fixtures/](fixtures/).

## Install and test

Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

`python -m mb_mcp --list-endpoints` prints the tool table without any network access.

## Live contract tests (opt-in, write to the target)

`tests/contract/test_demo.py` runs the verified sequence through the runtime against the public Mikbill demo: reads, reference data CRUD, a user's life cycle (payments, credit, freeze, disable, disconnect, reactivate, soft delete), catalogs, IP pool, real IPs, NAS, switches, groups. They are skipped unless you opt in, and they refuse any host not in `MB_CONTRACT_ALLOWED_HOSTS` (default `admin.mikbill.pro`):

```powershell
$env:MB_CONTRACT = "1"
$env:MB_CONTRACT_CONFIRM = "I_ACCEPT_WRITES_ON_DEMO"
$env:MB_BASE_URL = "https://admin.mikbill.pro"
$env:MB_LOGIN = "<demo login>"; $env:MB_PASSWORD = "<demo password>"
.\.venv\Scripts\python -m pytest tests/contract -v
```

All records are named `MCPTEST_<run id>`. Mikbill soft-deletes users, so each run leaves about four soft-deleted `mcpt...` users on the target (users are created in an existing house; no house is deleted while users reference it). If cleanup fails, the fixture prints the leftovers. Optional overrides for other installations: `MB_CONTRACT_SECTOR_ID`, `MB_CONTRACT_GID`, `MB_CONTRACT_VKL_GID`, `MB_CONTRACT_SERVICE_ID`.

## Configuration

Environment variables only. Never commit them.

| Variable | Meaning | Default |
|---|---|---|
| `MB_BASE_URL` | Mikbill admin URL, e.g. `https://admin.example.com` | required |
| `MB_LOGIN`, `MB_PASSWORD` | A dedicated, least-privileged Mikbill admin account | required |
| `MB_SCOPES` | Comma list of `read,write,finance,account_state,admin` | `read,write` |
| `MB_MAX_BULK` | Maximum uids per bulk call | `50` |
| `MB_CA_BUNDLE` | CA bundle for private certificates | system store |
| `MB_INSECURE_TLS` | `1` disables TLS verification (logged as a warning) | off |
| `MB_TIMEOUT_CONNECT`, `MB_TIMEOUT_READ` | Seconds | `5`, `30` |
| `MB_AUDIT_FILE` | Path for a JSON-lines audit log of writes and gated calls | off |
| `MB_HTTP_TOKENS` | HTTP only: JSON list `[{"id": "agent1", "sha256": "<hex>", "scopes": ["read"]}]` | none |
| `MB_ALLOWED_ORIGINS` | HTTP only: comma list of allowed `Origin` values | none |

The default scopes expose only read tools and the non-gated writes. The `finance`, `account_state`, and `admin` scopes are opt-in.

## Gated tools

Tools in `finance`, `account_state`, and `admin` take three extra arguments:

1. Call with `reason` (and no `dry_run`): the server returns a preview and a one-time `approval_token`. Nothing changes.
2. Repeat the call with `dry_run: false`, the `approval_token`, and the same parameters. The token is single use, expires after 5 minutes, and is bound to the exact parameters.

Soft deletes (`mb_delete_user`, `mb_bulk_delete_users`, `mb_delete_disconnected_user`) leave the account in the database with state 4. All three are gated. Passwords in Mikbill responses are masked in tool output.

## Run over stdio (Claude Code / Claude Desktop)

```json
{
  "mcpServers": {
    "mikbill": {
      "command": "C:\\path\\to\\mikbill-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mb_mcp"],
      "env": {
        "MB_BASE_URL": "https://admin.example.com",
        "MB_LOGIN": "mcp-agent",
        "MB_PASSWORD": "...",
        "MB_SCOPES": "read,write"
      }
    }
  }
}
```

## Run over Streamable HTTP

```powershell
$env:MB_HTTP_TOKENS = '[{"id":"agent1","sha256":"<sha256 of the token>","scopes":["read"]}]'
.\.venv\Scripts\python -m mb_mcp --http --host 127.0.0.1 --port 8765
```

Endpoint: `http://127.0.0.1:8765/mcp` with `Authorization: Bearer <token>`. Binds to localhost by default; put a TLS reverse proxy in front for remote access. A token only sees tools in its scopes, intersected with `MB_SCOPES`.

## Status

- Implemented: client, error mapping, gating, redaction, audit, runtime, stdio and HTTP servers. 105 tests pass, including an in-memory MCP session and a real HTTP round trip.
- The live contract suite (`tests/contract`, 7 tests) passes against the public Mikbill demo. It calls 76 of the 80 tools. Not covered live: `mb_get_user_short_history`, `mb_get_user_canvas_stat`, `mb_get_user_devices`, `mb_add_user_device`. Some paths are only partly covered: `mb_use_voucher` is tested with a wrong code only, and `mb_set_personal_service` only on a service without a portal.
- Open items are listed in SPEC.md ("Blocked and denied", "Discovered endpoints").
