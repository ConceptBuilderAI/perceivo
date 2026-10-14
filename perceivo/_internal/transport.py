"""The public-API transport: one request, one mode's headers, one error mapping.

Every public-API call goes through :func:`request`. It builds the request, applies
exactly one mode's headers, sends it once, and maps the answer:

* ``api_key``: ``X-API-Key``.
* ``hmac``: ``X-API-Key`` plus the four ``X-Perceivo-*`` headers, signed over the
  request exactly as built: the method, the raw target, the body bytes and the
  ``Content-Type`` header value. The organization is never sent, so canonical
  field 9 is empty.
* ``entra``: ``Authorization: Bearer`` and nothing else auth-related. The platform
  refuses a bearer request that also carries ``X-API-Key`` or a signature.

401 raises :class:`AuthenticationError`, 403 :class:`AccessDeniedError`, 5xx
:class:`ServiceError`; every other status is returned for the caller to interpret.
Only a 503 whose body is ``{"error": "service_unavailable", "reason":
"verification_unavailable"}`` is ``retryable``: request verification answers it
before the route runs, so nothing was done. Any other 5xx may have been answered
*after* the route ran. Nothing here retries, and redirects are never followed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..types.errors import (
    AccessDeniedError,
    AuthenticationError,
    ConfigurationError,
    ServiceError,
)
from ._entra import bearer_token
from .auth import AuthMode, ResolvedAuth

if TYPE_CHECKING:  # pragma: no cover
    import httpx

__all__ = ["MAX_HMAC_BODY_BYTES", "BodyTooLargeError", "request"]

#: The platform refuses a signed body over 1 MiB (``HMAC_MAX_BODY_BYTES``). The cap
#: applies to HMAC only; the other two modes are not pre-checked.
MAX_HMAC_BODY_BYTES = 1_048_576

_TIMEOUT_SECONDS = 60.0
_GENERIC_UNSAFE_REMEDY = "The request may have taken effect; check its result before retrying."
_RETRYABLE_BODY = {"error": "service_unavailable", "reason": "verification_unavailable"}

_UNAUTHORIZED = {
    AuthMode.API_KEY: (
        "The API key was not accepted. Check PERCEIVO_API_KEY is a current key of a "
        "service account in the target organization."
    ),
    AuthMode.HMAC: (
        "The signed request was not accepted. Check PERCEIVO_API_KEY, PERCEIVO_KEY_ID, "
        "PERCEIVO_SIGNING_SECRET and PERCEIVO_SIGNING_AUDIENCE belong together."
    ),
    AuthMode.ENTRA: (
        "The bearer token was not accepted. The service principal must be bound to a "
        "service account (tenant id and Enterprise Application object id), and "
        "PERCEIVO_TOKEN_SCOPE must be the Perceivo API's scope."
    ),
}
_REASONS = {
    "signature_required": (
        "This key only accepts signed requests (auth_mode=hmac). Set PERCEIVO_KEY_ID and "
        "PERCEIVO_SIGNING_SECRET, or use Credentials.hmac(...)."
    ),
    "invalid_signature": (
        "The signature did not verify. Check PERCEIVO_SIGNING_SECRET is the 64-hex secret "
        "enrolled for PERCEIVO_KEY_ID, and PERCEIVO_SIGNING_AUDIENCE is this deployment's "
        "audience (perceivo-api-prod for https://api.perceivo.ai)."
    ),
    "timestamp_skew": (
        "The local clock is more than 300 seconds from the server's. Synchronise the clock."
    ),
    "key_id_mismatch": (
        "PERCEIVO_KEY_ID is not the id of the key in PERCEIVO_API_KEY. Use the key id shown "
        "when its signing secret was enrolled."
    ),
    "unsignable_target": "The request target cannot be signed. Report this as an SDK defect.",
    "reserved_header_present": (
        "A reserved X-Perceivo-* header was sent where it is not allowed. Do not set "
        "X-Perceivo-* headers yourself."
    ),
    "nonce_format": "The request nonce was refused. Report this as an SDK defect.",
    "signature_malformed": "The signature header was malformed. Report this as an SDK defect.",
    "credential_in_query": (
        "A credential was sent in the query string. Credentials travel in headers only."
    ),
}
_FORBIDDEN = (
    "The service account may not do this. Check it holds the ORCHESTRATION_CREATE and "
    "COMPONENT_CREATE privileges, that any IP allowlist on the key includes this address, and, for Entra ID, that "
    "the service-principal binding is enabled."
)


class BodyTooLargeError(ConfigurationError):
    """An HMAC body over :data:`MAX_HMAC_BODY_BYTES`, refused before any I/O."""

    def __init__(self, size: int) -> None:
        super().__init__(
            f"the request body is {size} bytes; a signed (HMAC) request may carry at most "
            f"{MAX_HMAC_BODY_BYTES} bytes"
        )
        self.size = size


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _reason(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    value = body.get("reason") or body.get("error")
    return value if isinstance(value, str) else None


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None


def is_retryable(status: int, body: Any) -> bool:
    """Return ``True`` only for a 503 with the verification-unavailable body."""
    return (
        status == 503
        and isinstance(body, dict)
        and all(body.get(key) == value for key, value in _RETRYABLE_BODY.items())
    )


def raise_for_status(response: httpx.Response, mode: AuthMode, unsafe_remedy: str) -> None:
    """Raise the typed error for a 401, 403 or 5xx; return for anything else."""
    status = response.status_code
    if status not in (401, 403) and status < 500:
        return
    body = _body(response)
    reason = _reason(body)
    shown = f" ({reason})" if reason else ""
    if status == 401:
        remedy = _REASONS.get(reason or "", _UNAUTHORIZED[mode])
        raise AuthenticationError(
            f"{mode} authentication failed: the API answered 401{shown}. {remedy}",
            mode=str(mode),
            status_code=status,
            reason=reason,
            remedy=remedy,
        )
    if status == 403:
        raise AccessDeniedError(
            f"{mode}: the API answered 403{shown}. {_FORBIDDEN}",
            mode=str(mode),
            status_code=status,
            reason=reason,
            remedy=_FORBIDDEN,
        )
    retryable = is_retryable(status, body)
    retry_after = _retry_after(response) if retryable else None
    if retryable:
        wait = f" after {retry_after:g} seconds" if retry_after is not None else ""
        remedy = (
            "Request verification was unavailable, so the request was answered before it "
            f"ran and nothing was done. It is safe to retry{wait}."
        )
    else:
        remedy = unsafe_remedy
    raise ServiceError(
        f"{mode}: the API answered {status}{shown}. {remedy}",
        retryable=retryable,
        status_code=status,
        retry_after=retry_after,
        mode=str(mode),
        reason=reason,
        remedy=remedy,
    )


def _sign(request: httpx.Request, auth: ResolvedAuth) -> None:
    from .signing import SigningError, sign_request

    creds = auth.credentials
    try:
        signed = sign_request(
            method=request.method,
            target=request.url.raw_path,
            body=request.content,
            content_type=request.headers.get("Content-Type"),
            organization=None,
            key_id=str(creds.key_id),
            secret=str(creds.signing_secret),
            audience=str(creds.audience),
        )
    except SigningError as exc:
        raise ConfigurationError(f"hmac: the request cannot be signed: {exc}") from None
    request.headers.update(signed.headers)


async def request(
    auth: ResolvedAuth,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    unsafe_remedy: str = _GENERIC_UNSAFE_REMEDY,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.Response:
    """Send one public-API request in ``auth``'s mode and map auth and service errors.

    Args:
        auth: The resolved mode, credential and base URL.
        method: The HTTP method.
        path: The ``/v1/...`` path, including any query string.
        body: The exact bytes to send. They are the bytes that are signed.
        content_type: The ``Content-Type`` sent (and signed) with a body.
        unsafe_remedy: The remedy for a 5xx that may have been answered after the
            route ran.
        transport: An httpx transport; tests pass a ``MockTransport``.

    Raises:
        BodyTooLargeError: An HMAC body over 1 MiB, before any I/O.
        AuthenticationError, AccessDeniedError, ServiceError: see the module docstring.
    """
    import httpx

    if not path.startswith("/v1/"):
        raise ConfigurationError(f"public-API paths start with /v1/, got {path!r}")
    if auth.mode == AuthMode.HMAC and body is not None and len(body) > MAX_HMAC_BODY_BYTES:
        raise BodyTooLargeError(len(body))
    creds = auth.credentials
    headers = {"Content-Type": content_type} if body is not None else {}
    async with httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, follow_redirects=False, transport=transport
    ) as client:
        if auth.mode == AuthMode.ENTRA:
            headers["Authorization"] = f"Bearer {await bearer_token(creds, client)}"
        else:
            headers["X-API-Key"] = str(creds.key)
        built = client.build_request(
            method, f"{auth.base_url}{path}", content=body, headers=headers
        )
        if auth.mode == AuthMode.HMAC:
            _sign(built, auth)
        try:
            response = await client.send(built)
        except httpx.HTTPError as exc:
            raise ServiceError(
                f"{auth.mode}: the request did not complete ({type(exc).__name__}). "
                f"{unsafe_remedy}",
                mode=str(auth.mode),
                remedy=unsafe_remedy,
            ) from None
    raise_for_status(response, auth.mode, unsafe_remedy)
    return response
