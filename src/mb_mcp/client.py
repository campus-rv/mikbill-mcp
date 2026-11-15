"""Async Mikbill admin client: cookie session, lazy login, one re-login, normalized errors."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
import time
from collections.abc import Mapping
from typing import Any

import httpx

from .config import ConfigError, Settings
from .errors import MikbillError, error_from_envelope, is_unauth

log = logging.getLogger("mb_mcp.client")

AUTH_URI = "/extjs/index/auth"

# After Mikbill rejects a login, calls fail at once for this long instead of logging in again. Every tool call would
# otherwise retry the login, and a wrong password could lock the account on the Mikbill side.
LOGIN_COOLDOWN_SECONDS = 30.0


def _verify(settings: Settings) -> ssl.SSLContext | bool:
    """What httpx gets for `verify`: False (MB_INSECURE_TLS=1), an SSL context built from MB_CA_BUNDLE, or True.

    httpx 0.28 deprecates a file path for `verify`, so the bundle is loaded into a context here, which also makes an
    unreadable file a start-up error that names the variable.
    """
    if settings.insecure_tls:
        log.warning("TLS verification is disabled (MB_INSECURE_TLS=1)")
        return False
    if settings.ca_bundle:
        try:
            return ssl.create_default_context(cafile=settings.ca_bundle)
        except (OSError, ssl.SSLError) as exc:
            reason = getattr(exc, "strerror", None) or type(exc).__name__
            raise ConfigError(f"MB_CA_BUNDLE {settings.ca_bundle} cannot be loaded as a CA bundle: {reason}") from exc
    return True


class MikbillClient:
    """POSTs form-encoded requests to Mikbill and returns the decoded envelope.

    - Session state is the cookie jar. The JWT in the login response is not needed (SPEC.md).
    - Unauthenticated calls return HTTP 200 with success:false/error 1403. That triggers one
      re-login and one retry.
    - Every other failure is raised as MikbillError.
    """

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            verify=_verify(settings),
            timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout),
            follow_redirects=False,
            transport=transport,
        )
        self._lock = asyncio.Lock()
        self._logged_in = False
        self._generation = 0            # counts successful logins; a call that sees it change knows someone re-logged in
        self._login_blocked_until = 0.0  # time.monotonic() until which a rejected login is not tried again

    async def aclose(self) -> None:
        await self._http.aclose()

    async def login(self) -> None:
        """Authenticate. The password is MD5-hashed in memory, as the Mikbill protocol requires."""
        digest = hashlib.md5(self._settings.password.encode("utf-8")).hexdigest()  # noqa: S324 (the protocol)
        env = await self._post(AUTH_URI, {"login": self._settings.login, "password": digest})
        if env.get("success") is not True:
            self._logged_in = False
            raise MikbillError("auth_failed", "Mikbill rejected the login", upstream=None)
        self._logged_in = True
        self._generation += 1

    async def _login_locked(self) -> None:
        """Log in, with the lock held. A login Mikbill rejected starts a cool-down; timeouts and server errors do not."""
        now = time.monotonic()
        if now < self._login_blocked_until:
            wait = int(self._login_blocked_until - now) + 1
            raise MikbillError("auth_failed", f"Mikbill rejected the last login; not trying again for {wait} s "
                                              "(check MB_LOGIN and MB_PASSWORD)")
        try:
            await self.login()
        except MikbillError as err:
            if err.code == "auth_failed":
                self._login_blocked_until = time.monotonic() + LOGIN_COOLDOWN_SECONDS
            raise
        self._login_blocked_until = 0.0

    async def _ensure_login(self) -> None:
        if self._logged_in:
            return
        async with self._lock:
            if not self._logged_in:
                await self._login_locked()

    async def _relogin(self, seen_generation: int) -> None:
        """Log in again after a 1403, unless another call already did since `seen_generation` (one login, not one per
        concurrent call)."""
        async with self._lock:
            if self._generation != seen_generation and self._logged_in:
                return
            self._logged_in = False
            await self._login_locked()

    async def _post(self, uri: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
        # `maybe_applied` separates "the request never left" (no connection, no free pool slot) from "it left, or may
        # have, and no usable answer came back". For a write the second case has an unknown outcome, and the runtime
        # tells the caller so instead of inviting a retry (a retried payment is a double payment).
        try:
            resp = await self._http.post(uri, data=dict(params or {}))
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise MikbillError("timeout", f"could not connect to Mikbill for {uri} in time") from exc
        except httpx.TimeoutException as exc:
            raise MikbillError("timeout", f"request to {uri} timed out", maybe_applied=True) from exc
        except (httpx.ConnectError, httpx.UnsupportedProtocol, httpx.InvalidURL) as exc:
            raise MikbillError("upstream_error", f"request to {uri} failed: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise MikbillError("upstream_error", f"request to {uri} failed: {type(exc).__name__}",
                               maybe_applied=True) from exc
        if resp.status_code != 200:
            raise MikbillError("upstream_error", f"HTTP {resp.status_code} from {uri}", status=resp.status_code,
                               maybe_applied=True)
        try:
            body = resp.json()
        except ValueError as exc:
            raise MikbillError("upstream_error", f"non-JSON response from {uri}", status=resp.status_code,
                               maybe_applied=True) from exc
        if not isinstance(body, dict):
            raise MikbillError("upstream_error", f"unexpected JSON type from {uri}", status=resp.status_code,
                               maybe_applied=True)
        return body

    async def call(self, uri: str, params: Mapping[str, Any] | None = None, *, allow_empty: bool = False,
                   no_success_key: bool = False) -> dict[str, Any]:
        """Call `uri` and return the successful envelope. Raises MikbillError otherwise.

        `allow_empty` treats an empty `{}` body as an empty success (used by getuserdevicesbyuid).
        `no_success_key` accepts an envelope that has `data` but no `success` field (getticketslist2x).
        """
        await self._ensure_login()
        generation = self._generation
        env = await self._post(uri, params)
        if is_unauth(env):
            await self._relogin(generation)
            env = await self._post(uri, params)
        if allow_empty and env == {}:
            return {"success": True, "data": []}
        if no_success_key and "success" not in env and "data" in env:
            return {"success": True, **env}
        err = error_from_envelope(env)
        if err is not None:
            raise err
        return env
