"""Microsoft Entra ID client-credentials tokens for the public API (httpx only).

One form POST to ``https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token``
with ``grant_type=client_credentials``, authenticated by either a client secret or
a federated token read from a file (a ``client_assertion`` of type jwt-bearer). The
SDK never parses the token; the platform validates it.

**Cache isolation.** Tokens are cached in memory only, keyed by
``(authority host, tenant, client, scope, fingerprint)``, where the fingerprint is
``sha256(client_secret)`` or the real path of the token file. A token is served
only for the exact key that fetched it, so two service principals in one process
never share a bearer. The secret itself is never a key and is never logged.

**Concurrency.** The cache ``dict`` is guarded by one ``threading.Lock`` held only
for a get or a set, never across an ``await``. Concurrent refreshes of one key in
one event loop are collapsed onto an ``asyncio.Lock`` looked up per *running* loop,
so a later ``asyncio.run`` gets a fresh lock rather than one bound to a closed loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading
import time
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..types.errors import AuthenticationError, ConfigurationError, ServiceError

if TYPE_CHECKING:  # pragma: no cover
    import httpx

    from ..types._credentials import Credentials

__all__ = ["AUTHORITY_HOST", "REFRESH_MARGIN_SECONDS", "bearer_token", "cache_key"]

AUTHORITY_HOST = "login.microsoftonline.com"
REFRESH_MARGIN_SECONDS = 300
_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_AADSTS = re.compile(r"\bAADSTS\d+\b")

_REMEDIES = {
    "AADSTS7000215": (
        "The client secret is wrong or has expired. Create a new secret for the app "
        "registration and set PERCEIVO_CLIENT_SECRET to its value (not its id)."
    ),
    "AADSTS700016": (
        "The application was not found in the tenant. Check PERCEIVO_CLIENT_ID is the "
        "application (client) id and PERCEIVO_TENANT_ID is the tenant it is registered in."
    ),
    "AADSTS500011": (
        "The Perceivo API is not consented in this tenant. An administrator must grant "
        "admin consent for the Perceivo public API application, then retry."
    ),
}
_DEFAULT_REMEDY = (
    "Entra ID refused the token request. Check PERCEIVO_TENANT_ID, PERCEIVO_CLIENT_ID, "
    "the client secret or federated token file, and PERCEIVO_TOKEN_SCOPE."
)


@dataclass(frozen=True)
class _Entry:
    token: str
    expires_at: float


_cache: dict[tuple[str, ...], _Entry] = {}
_cache_lock = threading.Lock()
_loop_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple, asyncio.Lock]] = (  # type: ignore[type-arg]
    weakref.WeakKeyDictionary()
)


def cache_key(creds: Credentials) -> tuple[str, ...]:
    """Return the cache key for ``creds``. It never contains the secret itself."""
    if creds.client_secret is not None:
        fingerprint = ("secret", hashlib.sha256(creds.client_secret.encode("utf-8")).hexdigest())
    else:
        fingerprint = ("file", os.path.realpath(str(creds.federated_token_file)))
    return (
        AUTHORITY_HOST,
        str(creds.tenant_id),
        str(creds.client_id),
        str(creds.scope),
        *fingerprint,
    )


def _cached(key: tuple[str, ...], now: float) -> str | None:
    with _cache_lock:
        entry = _cache.get(key)
    if entry is not None and entry.expires_at - now > REFRESH_MARGIN_SECONDS:
        return entry.token
    return None


def _refresh_lock(key: tuple[str, ...]) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    with _cache_lock:
        locks = _loop_locks.setdefault(loop, {})
        lock = locks.get(key)
        if lock is None:
            lock = locks[key] = asyncio.Lock()
    return lock


def _form(creds: Credentials) -> dict[str, str]:
    form = {
        "grant_type": "client_credentials",
        "client_id": str(creds.client_id),
        "scope": str(creds.scope),
    }
    if creds.client_secret is not None:
        form["client_secret"] = creds.client_secret
    else:
        # Re-read on every acquisition: the kubelet rotates this file.
        try:
            with open(str(creds.federated_token_file), encoding="utf-8") as handle:
                assertion = handle.read().strip()
        except OSError as exc:
            raise ConfigurationError(
                f"entra: the federated token file {creds.federated_token_file} cannot be "
                f"read ({type(exc).__name__})"
            ) from None
        form["client_assertion_type"] = _ASSERTION_TYPE
        form["client_assertion"] = assertion
    return form


def _refusal(status: int, body: Any) -> AuthenticationError:
    description = body.get("error_description", "") if isinstance(body, dict) else ""
    codes = body.get("error_codes") if isinstance(body, dict) else None
    match = _AADSTS.search(description or "")
    if match:
        reason = match.group(0)
    elif isinstance(codes, list) and codes:
        reason = f"AADSTS{codes[0]}"
    else:
        reason = (body.get("error") if isinstance(body, dict) else None) or f"http_{status}"
    remedy = _REMEDIES.get(reason, _DEFAULT_REMEDY)
    return AuthenticationError(
        f"entra authentication failed: the token request was refused ({reason}). {remedy}",
        mode="entra",
        status_code=status,
        reason=reason,
        remedy=remedy,
    )


async def _fetch(creds: Credentials, client: httpx.AsyncClient, now: float) -> _Entry:
    import httpx

    url = f"https://{AUTHORITY_HOST}/{creds.tenant_id}/oauth2/v2.0/token"
    try:
        response = await client.post(url, data=_form(creds))
    except httpx.HTTPError as exc:
        raise ServiceError(
            f"entra: the token request did not complete ({type(exc).__name__}). No request "
            "reached the Perceivo API.",
            mode="entra",
            remedy="Check network access to login.microsoftonline.com.",
        ) from None
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200:
        if 400 <= response.status_code < 500:
            raise _refusal(response.status_code, body)
        raise ServiceError(
            f"entra: the token endpoint answered {response.status_code}. No request reached "
            "the Perceivo API.",
            status_code=response.status_code,
            mode="entra",
            remedy="The identity platform failed; try again later.",
        )
    token = body.get("access_token") if isinstance(body, dict) else None
    lifetime = body.get("expires_in") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise ServiceError(
            "entra: the token response carried no access_token",
            status_code=response.status_code,
            mode="entra",
        )
    try:
        seconds = float(lifetime)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        seconds = 0.0
    return _Entry(token=token, expires_at=now + seconds)


async def bearer_token(creds: Credentials, client: httpx.AsyncClient, *, clock=time.time) -> str:  # type: ignore[no-untyped-def]
    """Return a bearer for ``creds``, from the cache or one token request.

    A cached token is refreshed once fewer than 300 seconds of it remain.
    """
    key = cache_key(creds)
    token = _cached(key, clock())
    if token is not None:
        return token
    async with _refresh_lock(key):
        token = _cached(key, clock())
        if token is not None:
            return token
        entry = await _fetch(creds, client, clock())
        with _cache_lock:
            _cache[key] = entry
        return entry.token


def clear_cache() -> None:
    """Forget every cached token."""
    with _cache_lock:
        _cache.clear()
