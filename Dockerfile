# syntax=docker/dockerfile:1
# Mikbill admin MCP server. One image, two modes, chosen with MCP_TRANSPORT:
#
#   remote, Streamable HTTP on :8000 (the image default, MCP_TRANSPORT=http):
#     docker build -t mb-mcp:local .
#     docker run --rm -p 127.0.0.1:8000:8000 --env-file .env mb-mcp:local
#
#   local, stdio for an MCP client such as Claude Desktop (no port, no bearer token):
#     docker run -i --rm --env-file .env -e MCP_TRANSPORT=stdio mb-mcp:local
#
# Supply chain: the base image is pinned by digest and every Python package, the build tools included, is installed
# from a lock file with --require-hashes, so a build cannot pick up a version or a file that was not reviewed.
# Dependabot (.github/dependabot.yml) proposes the digest and action updates; scripts/lock.sh regenerates the locks.

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build
WORKDIR /src
COPY requirements-build.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-build.lock
COPY pyproject.toml README.md ./
COPY src ./src
# --no-isolation: build with the pinned tools installed above instead of fetching unpinned ones into a build env
RUN python -m build --wheel --no-isolation --outdir /dist
# The runtime tree is assembled here, so the final image needs no pip: dependencies from the lock with hashes,
# then the project's own wheel without resolving anything again.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --prefix=/install -r requirements.lock \
    && pip install --no-cache-dir --no-deps --prefix=/install /dist/*.whl

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=http \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8000
# Debian security updates released after the pinned base image was built (zlib, which decodes Mikbill's compressed
# responses, had one at the time of writing). This is the one step that is not pinned: a fixed OS package matters more
# here than a byte-identical rebuild, and the CI image scan fails the build when a fix exists and is not installed.
# /data is writable by the service user, for MB_AUDIT_FILE=/data/audit.jsonl on a volume.
# pip is removed: nothing is installed at run time, and its vendored libraries (urllib3, msgpack) are what an image
# scan otherwise reports.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home mbmcp \
    && mkdir /data && chown mbmcp /data \
    && python -m pip uninstall --yes --quiet --root-user-action=ignore pip
COPY --from=build /install /usr/local
USER mbmcp
EXPOSE 8000
# In http mode: GET /health, which answers 200 without a token (urlopen raises on any other status). It checks
# that the HTTP server is up, not the Mikbill upstream, and follows MCP_HTTP_PORT. It uses the image's Python, so
# no curl or wget is installed. In stdio mode there is no server to probe, so the check passes.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; os.environ.get('MCP_TRANSPORT') != 'http' or u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('MCP_HTTP_PORT', '8000'), timeout=2)"]
ENTRYPOINT ["mb-mcp"]
