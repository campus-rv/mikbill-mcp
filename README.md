# mb-mcp

MCP server for the Mikbill admin API. 95 tools in five scopes, including tickets and user restore, with opt-in scopes, a reason and an audit log for everything that changes billing, account state or privileges, and MCP tool annotations so the client asks the user before it runs them. The design is in [SPEC.md](SPEC.md); per-endpoint evidence from the public demo is in [fixtures/](fixtures/).

## Install and test

Python 3.12 or newer. CI tests 3.12, 3.14 and 3.15; the Docker image uses 3.15.

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
| `MB_SCOPES` | Comma list of `read,write,finance,account,admin` | `read,write` |
| `MB_MAX_BULK` | Maximum uids per bulk call | `50` |
| `MB_CA_BUNDLE` | CA bundle for private certificates | system store |
| `MB_INSECURE_TLS` | `1` disables TLS verification (logged as a warning) | off |
| `MB_TIMEOUT_CONNECT`, `MB_TIMEOUT_READ` | Seconds | `5`, `30` |
| `MB_AUDIT_FILE` | Path for a JSON-lines audit log of writes and gated calls | off |
| `MCP_TRANSPORT` | `stdio` (a local MCP client starts the server) or `http` (Streamable HTTP). `--transport` or `--http` override it. The Docker image sets `http` | `stdio` |
| `MCP_HTTP_BEARER_TOKEN` | HTTP only: one plain bearer token (at least 16 characters) that may use every scope in `MB_SCOPES`. Only its SHA-256 is kept in memory | none |
| `MB_HTTP_TOKENS` | HTTP only: JSON list `[{"id": "agent1", "sha256": "<hex>", "scopes": ["read"]}]`, for tokens with different scopes. Can be combined with `MCP_HTTP_BEARER_TOKEN` | none |
| `MCP_HTTP_HOST`, `MCP_HTTP_PORT` | HTTP only: bind address and port. `--host` and `--port` override them. The Docker image sets `0.0.0.0` and `8000` | `127.0.0.1`, `8765` |
| `MCP_ALLOWED_ORIGINS` | HTTP only: comma list of allowed `Origin` values | none |
| `MCP_ALLOWED_HOSTS` | HTTP only: extra allowed `Host` header values (`host:port`), for proxies and port mappings | none |

The variables marked "HTTP only" are read only when the transport is `http`. In stdio mode they are ignored and not validated, so one env file can serve both modes.

The default scopes (`read`, `write`) expose the read tools and the non-gated writes. The `finance`, `account` and `admin` scopes are opt-in. (`account` was named `account_state` in 0.0.1; the old name is no longer accepted.)

## Gated tools

Tools in `finance`, `account` and `admin` are off by default and take two extra arguments:

- `reason` (required, at least 3 characters): why the action is needed. It goes to the audit log.
- `dry_run` (optional): `true` returns a preview (the exact request, the targets with their current state, totals, warnings) and sends nothing. It defaults to `true` for bulk tools (`mb_bulk_*`, which take `uids`), so a first call previews and `dry_run: false` executes. Single-target tools execute on the first call.

There is no approval token. The user's approval of a call is the client's job, and every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) so the client can decide when to ask. Annotations are hints, not a safety boundary: the scopes, `reason`, the audit log and `MB_MAX_BULK` are the server's controls. A token or env with a gated scope can run those tools without a human, so grant them only to clients that ask the user.

Claude Code: gated tools prompt by default, and you can make that explicit with `permissions.ask` rules in `.claude/settings.json` (the tool name is `mcp__<server name>__<tool>`; `mikbill` below is the name from `claude mcp add mikbill ...`). Generate the list from the endpoint table:

```bash
python -m mb_mcp --list-endpoints | awk '$1=="finance"||$1=="account"||$1=="admin" {printf "      \"mcp__mikbill__%s\",
", $2}'
```

```json
{
  "permissions": {
    "ask": ["mcp__mikbill__mb_create_payment", "mcp__mikbill__mb_bulk_payment", "mcp__mikbill__mb_delete_user"]
  }
}
```

Soft deletes (`mb_delete_user`, `mb_bulk_delete_users`, `mb_delete_disconnected_user`) leave the account in the database with state 4; `mb_restore_user` brings it back. Deletes of sticky notes, user devices, tickets and ticket notes are in the `write` scope and run without `reason` or preview. Passwords in Mikbill responses are masked in tool output.

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
$env:MCP_HTTP_BEARER_TOKEN = '<a secret of at least 16 characters>'
.\.venv\Scripts\python -m mb_mcp --http          # or MCP_TRANSPORT=http; host and port from MCP_HTTP_HOST / MCP_HTTP_PORT, or --host / --port
```

Endpoint: `http://127.0.0.1:8765/mcp` with `Authorization: Bearer <token>`. At least one token is required: `MCP_HTTP_BEARER_TOKEN` (one token with all configured scopes) or `MB_HTTP_TOKENS` (several tokens with their own scopes, stored as SHA-256 hashes). A token only sees tools in its scopes, intersected with `MB_SCOPES`. Binds to localhost by default; put a TLS reverse proxy in front for remote access.

`GET /health` answers `200 {"status":"ok"}` without a token. It only tells you the HTTP server is up; it does not call Mikbill and reveals no configuration.

## Docker

One image serves both modes, and `MCP_TRANSPORT` picks the mode: `http` (the image default, remote Streamable HTTP) or `stdio` (a local MCP client starts the container).

**Image.** The CI workflow ([.github/workflows/ci.yml](.github/workflows/ci.yml)) builds it for `linux/amd64` and `linux/arm64`, runs the unit and smoke tests, and publishes it to GitHub Container Registry:

| Tag | Published when |
|---|---|
| `ghcr.io/campus-rv/mikbill-mcp:edge`, `:sha-<commit>` | every push to `main` |
| `:1.2.3`, `:1.2`, `:latest`, `:sha-<commit>` | a `v1.2.3` tag is pushed |

Pull requests build and test the image but do not publish it. A new ghcr.io package is private until you make it public in the package settings; until then run `docker login ghcr.io` with a token that has `read:packages`. Or build it yourself: `docker build -t mb-mcp:local .`. The examples below use `ghcr.io/campus-rv/mikbill-mcp:latest`; use `mb-mcp:local` if you built it.

All examples read an env file. Start from [examples/.env.example](examples/.env.example) (`cp examples/.env.example .env`) and set `MB_BASE_URL`, `MB_LOGIN`, `MB_PASSWORD`, and for HTTP a token: `MCP_HTTP_BEARER_TOKEN=$(openssl rand -hex 32)`.

### Remote: Streamable HTTP

```bash
docker run -d --name mikbill-mcp --restart unless-stopped \
  -p 127.0.0.1:8000:8000 --env-file .env \
  ghcr.io/campus-rv/mikbill-mcp:latest

docker ps --filter name=mikbill-mcp          # STATUS shows (healthy) after a few seconds
curl http://127.0.0.1:8000/health            # {"status":"ok"}, no token needed
```

List the tools your token can see:

```bash
curl -s http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer $MCP_HTTP_BEARER_TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Connect Claude Code to it:

```bash
claude mcp add --transport http mikbill http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer $MCP_HTTP_BEARER_TOKEN"
```

Other examples:

```bash
# More scopes for the one bearer token (gated scopes need a reason on every call; the client asks the user to approve)
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -e MB_SCOPES=read,write,finance ghcr.io/campus-rv/mikbill-mcp:latest

# Different tokens with different scopes: MB_HTTP_TOKENS holds the SHA-256 of each token, not the token
printf %s "$READER_TOKEN" | sha256sum        # put the hex digest into the JSON below
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env \
  -e MB_SCOPES=read,write,finance \
  -e 'MB_HTTP_TOKENS=[{"id":"reader","sha256":"<hex>","scopes":["read"]},{"id":"billing","sha256":"<hex>","scopes":["read","finance"]}]' \
  ghcr.io/campus-rv/mikbill-mcp:latest

# Another host port: the Host header must be allowed
docker run --rm -p 127.0.0.1:9000:8000 --env-file .env -e MCP_ALLOWED_HOSTS=127.0.0.1:9000 ghcr.io/campus-rv/mikbill-mcp:latest

# Another port inside the container (the health check follows it)
docker run --rm -p 127.0.0.1:9000:9000 --env-file .env -e MCP_HTTP_PORT=9000 ghcr.io/campus-rv/mikbill-mcp:latest

# Audit log on a volume (/data is writable by the image's non-root user)
docker run -d -p 127.0.0.1:8000:8000 --env-file .env -v mikbill-audit:/data -e MB_AUDIT_FILE=/data/audit.jsonl \
  ghcr.io/campus-rv/mikbill-mcp:latest

# Mikbill with a private CA
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -v "$PWD/ca.pem:/certs/ca.pem:ro" -e MB_CA_BUNDLE=/certs/ca.pem \
  ghcr.io/campus-rv/mikbill-mcp:latest
```

### Local: stdio

The MCP client starts the container and talks over stdin and stdout, so `-i` is required and there is no port or bearer token. The HTTP-only variables in the same env file are ignored in this mode.

```bash
docker run -i --rm --env-file .env -e MCP_TRANSPORT=stdio ghcr.io/campus-rv/mikbill-mcp:latest
```

Claude Code:

```bash
claude mcp add mikbill -- docker run -i --rm --env-file /abs/path/.env -e MCP_TRANSPORT=stdio ghcr.io/campus-rv/mikbill-mcp:latest
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mikbill": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--env-file", "C:\path\to\.env", "-e", "MCP_TRANSPORT=stdio",
               "ghcr.io/campus-rv/mikbill-mcp:latest"]
    }
  }
}
```

### Docker Compose

[examples/docker-compose.yml](examples/docker-compose.yml) runs the HTTP mode with a restart policy, a volume for the audit log, a read-only root file system, no capabilities and `no-new-privileges`:

```yaml
services:
  mikbill-mcp:
    image: ${MB_MCP_IMAGE:-ghcr.io/campus-rv/mikbill-mcp:latest}
    env_file: .env
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - audit:/data          # MB_AUDIT_FILE=/data/audit.jsonl
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true

volumes:
  audit:
```

```bash
cd examples
cp .env.example .env         # edit it
docker compose up -d
docker compose ps            # STATUS: healthy
curl http://127.0.0.1:8000/health
docker compose exec mikbill-mcp cat /data/audit.jsonl
```

To build from a checkout instead of pulling, uncomment `build: {context: ..}` in the file and run `docker compose up -d --build`. To serve other machines, run a TLS reverse proxy on the same Compose network, remove `ports`, and set `MCP_ALLOWED_HOSTS` to the proxy's public name (for example `mcp.example.com`).

### Details

The image (python:3.15-slim, about 250 MB, non-root user `mbmcp`) sets `MCP_TRANSPORT=http`, `MCP_HTTP_HOST=0.0.0.0` and `MCP_HTTP_PORT=8000` and serves Streamable HTTP at `:8000/mcp`. The Docker health check requests `http://127.0.0.1:$MCP_HTTP_PORT/health` with the image's own Python in http mode (no curl or wget in the image); in stdio mode there is no server to probe, so it passes. Because the server binds `0.0.0.0` inside the container, publish the port on `127.0.0.1` or put a TLS proxy in front. The server rejects requests whose `Host` header is not `localhost:<port>`, `127.0.0.1:<port>` or listed in `MCP_ALLOWED_HOSTS` (comma list of `host:port`). The health check calls `127.0.0.1`, so do not set `MCP_HTTP_HOST` to one specific non-loopback address.

### Tests and CI

Unit tests run without Docker. The smoke test needs a local Docker daemon: it builds the image, starts it against a fake Mikbill on the host, and checks HTTP, bearer-token auth, scopes, gating, `/health`, the Docker health check, a changed port, the audit volume under the Compose hardening flags, and stdio mode:

```bash
MB_SMOKE=1 python -m pytest tests/smoke -v
```

Set `MB_SMOKE_IMAGE=<name>` to test an image you already built. CI runs the unit tests on Python 3.12, 3.14 and 3.15, runs this smoke test on the amd64 image, starts the arm64 image under QEMU and checks `/health`, and then pushes the multi-arch image (not for pull requests).

## Status

- Implemented: client, error mapping, gating, redaction, audit, runtime, stdio and HTTP servers, Docker image. 289 unit tests pass, including an in-memory MCP session and a real HTTP round trip. 22 Docker smoke tests pass.
- The live contract suite (`tests/contract`) passes against the public Mikbill demo. All 10 tests passed on the last run, including the tests for ticket lookups, user history, devices and restore. Some paths are only partly covered: `mb_use_voucher` is tested with a wrong code only, and `mb_set_personal_service` only on a service without a portal.
- Open items are listed in SPEC.md ("Blocked and denied", "Discovered endpoints").
