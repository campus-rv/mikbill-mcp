# syntax=docker/dockerfile:1
# Mikbill admin MCP server. One image, two modes, chosen with MCP_TRANSPORT:
#
#   remote, Streamable HTTP on :8000 (the image default, MCP_TRANSPORT=http):
#     docker build -t mb-mcp:local .
#     docker run --rm -p 127.0.0.1:8000:8000 --env-file .env mb-mcp:local
#
#   local, stdio for an MCP client such as Claude Desktop (no port, no bearer token):
#     docker run -i --rm --env-file .env -e MCP_TRANSPORT=stdio mb-mcp:local

FROM python:3.15-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

FROM python:3.15-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MCP_TRANSPORT=http \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8000
# /data is writable by the service user, for MB_AUDIT_FILE=/data/audit.jsonl on a volume
RUN useradd --system --uid 10001 --no-create-home mbmcp \
    && mkdir /data && chown mbmcp /data
COPY --from=build /dist/*.whl /tmp/
RUN pip install /tmp/*.whl && rm -f /tmp/*.whl
USER mbmcp
EXPOSE 8000
# In http mode: GET /health, which answers 200 without a token (urlopen raises on any other status). It checks
# that the HTTP server is up, not the Mikbill upstream, and follows MCP_HTTP_PORT. It uses the image's Python, so
# no curl or wget is installed. In stdio mode there is no server to probe, so the check passes.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; os.environ.get('MCP_TRANSPORT') != 'http' or u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('MCP_HTTP_PORT', '8000'), timeout=2)"]
ENTRYPOINT ["mb-mcp"]
