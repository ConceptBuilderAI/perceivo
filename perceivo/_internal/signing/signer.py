"""Signing one request: the canonical assembly, the headers, and the signer object.

This module PRODUCES signature material. Everything it hands out is derived from
a request the caller already holds, and the request-level API here --
:func:`sign_request` and :class:`RequestSigner` -- is the entry point almost every
caller wants.

Three neighbours carry the rest of the keyed surface, re-exported below so
``from perceivo._internal.signing.signer import <anything>`` resolves here,
handing back the SAME objects:

* ``_verify`` -- :func:`compute_signature`, :func:`verify_signature` and
  :func:`verify_signed_request`: the CONSUMING side, predicates over
  attacker-influenced input that are total by contract.
* ``_credentials`` -- :class:`SigningCredentials` and
  :func:`resolve_signing_credentials`: the long-lived configured object, its
  containment rules, and where one may come from.
* ``_secret`` -- how a secret *spelling* becomes key bytes.

⛔ One rule holds across all four: the secret is never logged, never
interpolated into an exception message, and never rendered by ``repr()``;
comparison is always ``hmac.compare_digest``, never ``==``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import unquote as _urllib_unquote

from perceivo._internal.signing._constants import (
    HEADER_KEY_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    BodyType,
    ContentTypeType,
    OrganizationType,
    SecretType,
)
from perceivo._internal.signing._credentials import (
    SigningCredentials,
    _validate_key_id,
    resolve_signing_credentials,
)
from perceivo._internal.signing._secret import (
    # Aliased so ``signer._secret_bytes`` resolves: ``_constants`` cites it under
    # exactly that path. It is unused here because its callers are ``_verify``
    # and ``_credentials``.
    secret_to_key_bytes as _secret_bytes,  # noqa: F401 - re-export for a documented citation
)
from perceivo._internal.signing._verify import (
    compute_signature,
    verify_signature,
    verify_signed_request,
)
from perceivo._internal.signing.canonical import (
    SigningError,
    _canonical_nonce,
    _canonical_timestamp,
    build_canonical_request,
    generate_nonce,
    split_target,
)

__all__ = [
    "RequestSigner",
    "SignedRequest",
    "SigningCredentials",
    "compute_signature",
    "resolve_signing_credentials",
    "sign_request",
    "verify_signature",
    "verify_signed_request",
]


# Separators a permissive server may honour between query elements. `;` is legacy
# but still parsed by several stacks, so a credential hidden behind one must not
# slip past this guard.
_QUERY_SEPARATORS = re.compile(r"[&;]")

# The query parameter the platform reads a credential from. Names are compared
# AFTER normalisation,
# so `API_KEY`, `api%5Fkey` and a leading-space variant all fold into this entry.
_CREDENTIAL_PARAM_NAMES = frozenset({"api_key"})


@dataclass(frozen=True)
class SignedRequest:
    """The signature material for one request attempt.

    ``canonical_request`` carries no secret and is safe to log — it is the first
    thing to compare when two implementations disagree.
    """

    key_id: str
    timestamp: str
    nonce: str
    signature: str
    canonical_request: str
    #: The audience, Content-Type and organization that were signed. None travels
    #: in an ``X-Perceivo-*`` header — the audience because it is server-assigned
    #: configuration on both ends, the other two because the request already
    #: carries them — but all three are what a mismatch debugging session needs
    #: first.
    #:
    #: ⛔ ``organization`` is reported here and is deliberately NOT emitted by
    #: :attr:`headers`. The signer commits to the header the caller sends; it never
    #: supplies one. A signer that emitted it would let a caller sign an
    #: organization the request does not carry, which is the disagreement the
    #: verifier's post-MAC binding exists to refuse.
    audience: str = ""
    content_type: str = ""
    organization: str = ""

    @property
    def headers(self) -> dict[str, str]:
        """The four ``X-Perceivo-*`` headers to add to the request."""
        return {
            HEADER_KEY_ID: self.key_id,
            HEADER_TIMESTAMP: self.timestamp,
            HEADER_NONCE: self.nonce,
            HEADER_SIGNATURE: self.signature,
        }


def _normalise_query_param_name(raw_name: str) -> str:
    """Fold one query parameter name to the form a permissive server would see.

    Percent-decoding is deliberately **lenient**: a malformed escape is left as
    written rather than raising. This is a guard over the caller's own URL, and a
    guard that throws on odd input is a guard people delete.
    """
    name = raw_name.strip().replace("+", " ")
    if "%" in name:
        # unquote() never raises; an invalid escape survives verbatim, which is
        # the conservative direction here (we would rather over-match than miss).
        name = _urllib_unquote(name)
    return name.strip().lower()


def _reject_api_key_in_query(query: str) -> None:
    """Refuse to sign a request carrying a credential in its query string.

    ⛔ **Match the way a permissive server parses, not the way a well-formed client
    writes.** The check exists because a credential in a URL leaks into access logs,
    proxy logs, browser history and ``Referer`` — and it leaks there under whatever
    spelling the server was willing to accept. Comparing the raw bytes to the single
    literal ``api_key`` would miss every one of these, each of which a real server reads
    as ``api_key``:

    ==========================  ================================================
    ``API_KEY=…``               parameter names are not case-sensitive by contract
    ``api%5Fkey=…``             ``%5F`` is ``_``; frameworks decode names, not just
                                values — this is the form that actually matters
    ``?api_key=…`` w/ space     a leading space survives into the first element
    ``a=1;api_key=…``           ``;`` is a legacy separator PHP/Java still honour
    ==========================  ================================================

    Over-matching is the safe direction: the cost of a false positive is one clear
    error telling the caller to move a credential into a header.
    """
    for element in _QUERY_SEPARATORS.split(query):
        name = _normalise_query_param_name(element.partition("=")[0])
        if name in _CREDENTIAL_PARAM_NAMES:
            # Report the NORMALISED name, never the caller's raw spelling: the raw
            # element is attacker-influenceable and this message reaches logs.
            raise SigningError(
                f"a signed request must not carry '{name}' in the query string "
                "(any spelling a server would decode to it); send the credential "
                "in the X-API-Key header"
            )


def sign_request(
    *,
    method: str,
    target: bytes | str,
    body: BodyType = None,
    content_type: ContentTypeType,
    organization: OrganizationType,
    key_id: str,
    secret: SecretType,
    audience: str,
    timestamp: int | str | None = None,
    nonce: str | None = None,
) -> SignedRequest:
    """Sign one request and return its headers and canonical string.

    ⚠️ **Call this after the request is final.** ``content_type`` must be the
    header the request will actually carry, and for a multipart body the HTTP
    client generates the boundary parameter itself — sign before it does and every
    upload 401s. Build the request with your HTTP client first, then sign it.

    Args:
        method: Uppercase HTTP method, exactly as it will be sent.
        target: Origin-form request target including any query string, e.g.
            ``/api/v1/orchestrations?limit=10``.
        body: The exact bytes that will be sent, or ``None`` for no body.
        content_type: The ``Content-Type`` header **verbatim**, or ``None`` if the
            request carries none. Required — there is no default, because the
            wrong value here is a 401 with no other symptom.
        organization: The ``Perceivo-Organization`` header **verbatim**, or
            ``None`` if the request carries none. Required, and deliberately
            without a default: a defaulted ``""`` compares equal on both sides and
            silently unbinds canonical field 9, so every test would pass while the
            cross-tenant replay defence delivered nothing. It must be the lowercase
            hyphenated UUID you will actually send — it is rejected, never
            normalised.
        key_id: The ``api_keys.id`` UUID. Normalised to the lowercase hyphenated
            form before it is signed and sent.
        secret: The signing secret, disclosed once at key creation. Pass the
            disclosure verbatim — 64 lowercase hex characters, which
            :func:`_secret_bytes` decodes to the 32 bytes the server keys on. Pass
            ``bytes`` only if you hold the key material itself.
        audience: The deployment's configured audience, e.g.
            ``perceivo-api-prod``. Required, and never derived from a URL or a
            ``Host`` header.
        timestamp: Unix seconds. Defaults to the current time.
        nonce: Defaults to a fresh CSPRNG nonce. Never reuse one.

    Raises:
        SigningError: On any unsignable input. The message never contains the
            secret.
    """
    raw_path, query = split_target(target)
    _reject_api_key_in_query(query)
    resolved_ts = _canonical_timestamp(int(time.time()) if timestamp is None else timestamp)
    resolved_nonce = _canonical_nonce(generate_nonce() if nonce is None else nonce)
    resolved_key_id = _validate_key_id(key_id)
    canonical = build_canonical_request(
        audience=audience,
        key_id=resolved_key_id,
        method=method,
        raw_path=raw_path,
        query_string=query,
        body=body,
        content_type=content_type,
        organization=organization,
        timestamp=resolved_ts,
        nonce=resolved_nonce,
    )
    fields = canonical.split("\n")
    return SignedRequest(
        key_id=resolved_key_id,
        timestamp=resolved_ts,
        nonce=resolved_nonce,
        signature=compute_signature(canonical, secret),
        canonical_request=canonical,
        audience=fields[1],
        content_type=fields[7],
        organization=fields[8],
    )


class RequestSigner:
    """Signs requests for one key. Reusable, thread-safe, and secret-redacting.

    A fresh timestamp and a fresh nonce are generated on **every** call, which is
    what makes it correct to call this **inside** a transport's retry loop: a
    retry that replayed the previous nonce would be rejected as a replay, turning
    a transient outage into a permanent failure.
    """

    __slots__ = ("_clock", "_credentials", "_nonce_factory")

    def __init__(
        self,
        credentials: SigningCredentials,
        *,
        nonce_factory=generate_nonce,
        clock=time.time,
    ) -> None:
        if not isinstance(credentials, SigningCredentials):
            raise SigningError("credentials must be a SigningCredentials instance")
        self._credentials = credentials
        self._nonce_factory = nonce_factory
        self._clock = clock

    @property
    def key_id(self) -> str:
        return self._credentials.key_id

    @property
    def audience(self) -> str:
        return self._credentials.audience

    def sign(
        self,
        method: str,
        target: bytes | str,
        body: BodyType = None,
        *,
        content_type: ContentTypeType,
        organization: OrganizationType,
    ) -> SignedRequest:
        """Sign one attempt, with a fresh timestamp and nonce.

        ``content_type`` and ``organization`` are keyword-only and **required**:
        pass the headers the request will carry, or ``None`` if it carries none.
        The audience and key id come from the credentials, so neither can be
        forgotten here.

        ⛔ ``organization`` gets no default here either. The credentials name a
        key, not a tenant: the acting organization varies **per request**, so
        caching one on the signer would sign a tenant the request may not name.
        """
        return sign_request(
            method=method,
            target=target,
            body=body,
            content_type=content_type,
            organization=organization,
            key_id=self._credentials.key_id,
            secret=self._credentials.signing_secret,
            audience=self._credentials.audience,
            timestamp=int(self._clock()),
            nonce=self._nonce_factory(),
        )

    def __repr__(self) -> str:
        return (
            f"RequestSigner(key_id={self._credentials.key_id!r}, signing_secret=<redacted>, "
            f"audience={self._credentials.audience!r})"
        )

    __str__ = __repr__
