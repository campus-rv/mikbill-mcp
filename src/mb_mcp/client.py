"""Async Mikbill admin client: cookie session, lazy login, one re-login, normalized errors."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Mapping

import httpx

from .config import Settings
from .errors import MikbillError, error_from_envelope, is_unauth

log = logging.getLogger("mb_mcp.client")

AUTH_URI = "/extjs/index/auth"


class MikbillClient:
    """POSTs form-encoded requests to Mikbill and returns the decoded envelope.

    - Session state is the cookie jar. The JWT in the login response is not needed (SPEC.md).
    - Unauthenticated calls return HTTP 200 with success:false/error 1403. That triggers one
      re-login and one retry.
    - Every other failure is raised as MikbillError.
    """

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        if settings.insecure_tls:
            log.warning("TLS verification is disabled (MB_INSECURE_TLS=1)")
            verify: Any = False
        else:
            verify = settings.ca_bundle or True
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            verify=verify,
            timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout),
            follow_redirects=False,
            transport=transport,
        )
        self._lock = asyncio.Lock()
        self._logged_in = False

    async def aclose(self) -> None:
        await self._http.aclose()

    async def login(self) -> None:
        """Authenticate. The password is MD5-hashed in memory, as the Mikbill protocol requires."""
        digest = hashlib.md5(self._settings.password.encode("utf-8")).hexdigest()  # noqa: S324 (protocol)
        env = await self._post(AUTH_URI, {"login": self._settings.login, "password": digest})
        if env.get("success") is not True:
            self._logged_in = False
            raise MikbillError("auth_failed", "Mikbill rejected the login", upstream=None)
        self._logged_in = True

    async def _ensure_login(self) -> None:
        if self._logged_in:
            return
        async with self._lock:
            if not self._logged_in:
                await self.login()

    async def _relogin(self) -> None:
        async with self._lock:
            self._logged_in = False
            await self.login()

    async def _post(self, uri: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
        try:
            resp = await self._http.post(uri, data=dict(params or {}))
        except httpx.TimeoutException as exc:
            raise MikbillError("timeout", f"request to {uri} timed out") from exc
        except httpx.HTTPError as exc:
            raise MikbillError("upstream_error", f"request to {uri} failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise MikbillError("upstream_error", f"HTTP {resp.status_code} from {uri}", status=resp.status_code)
        try:
            body = resp.json()
        except ValueError as exc:
            raise MikbillError("upstream_error", f"non-JSON response from {uri}", status=resp.status_code) from exc
        if not isinstance(body, dict):
            raise MikbillError("upstream_error", f"unexpected JSON type from {uri}", status=resp.status_code)
        return body

    async def call(self, uri: str, params: Mapping[str, Any] | None = None, *, allow_empty: bool = False) -> dict[str, Any]:
        """Call `uri` and return the successful envelope. Raises MikbillError otherwise.

        `allow_empty` treats an empty `{}` body as an empty success (used by getuserdevicesbyuid).
        """
        await self._ensure_login()
        env = await self._post(uri, params)
        if is_unauth(env):
            await self._relogin()
            env = await self._post(uri, params)
        if allow_empty and env == {}:
            return {"success": True, "data": []}
        err = error_from_envelope(env)
        if err is not None:
            raise err
        return env
