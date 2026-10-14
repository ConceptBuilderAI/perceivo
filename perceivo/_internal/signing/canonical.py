"""Canonicalization: turn a request into the exact string that gets signed.

Pure functions over bytes. No secrets pass through this module, so everything
here is safe to log when two implementations disagree — printing the canonical
request is the first debugging step for a signature mismatch.

The full ruleset, with the reasoning for each ruling, is in the package docstring
(``perceivo._internal.signing``). Each rule is pinned by a test.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid

from perceivo._internal.signing._constants import (
    CANONICAL_AUDIENCE_PATTERN,
    CANONICAL_DOMAIN,
    EMPTY_BODY_SHA256,
    BodyType,
    ContentTypeType,
    OrganizationType,
)
from perceivo._internal.signing._errors import SigningError
from perceivo._internal.signing._header_fields import (
    # Re-exported so this module stays the documented import path:
    # `from ...signing.canonical import canonical_content_type` resolves here,
    # and it is the SAME object `_header_fields` defines.
    canonical_content_type,
    canonical_organization,
)
from perceivo._internal.signing._target import (
    # Re-exported so this module stays the documented import path:
    # `from ...signing.canonical import canonical_path` and the two private
    # helpers the vector tests reach for resolve here, and they are the SAME
    # objects `_target` defines.
    _decode_unreserved,  # noqa: F401  # re-export; the vector tests import it from here
    _remove_dot_segments,  # noqa: F401  # re-export; the tests import it from here
    canonical_path,
    canonical_query,
    split_target,
)

__all__ = [
    "SigningError",
    "body_sha256_hex",
    "build_canonical_request",
    "canonical_audience",
    "canonical_content_type",
    "canonical_key_id",
    "canonical_organization",
    "canonical_path",
    "canonical_query",
    "generate_nonce",
    "split_target",
]

# ``\A``/``\Z``, never ``^``/``$``. Python's ``$`` matches at end-of-string *or
# immediately before a trailing newline*, so ``^...$`` would accept a value ending
# in ``\n`` — the byte this module uses as the field separator. A field carrying a
# separator adds a twelfth field to an eleven-field string, which is how two different
# requests (``/a\n`` and ``/a?\n``) come to share one canonical string and one
# signature. ``\Z`` is end-of-string and nothing else.
_AUDIENCE_RE = re.compile(CANONICAL_AUDIENCE_PATTERN)
_METHOD_RE = re.compile(r"\A[A-Z]+\Z")
_NONCE_RE = re.compile(r"\A[A-Za-z0-9_-]{8,128}\Z")
_TIMESTAMP_RE = re.compile(r"\A(0|[1-9][0-9]*)\Z")


def body_sha256_hex(body: BodyType) -> str:
    """Return the lowercase hex SHA-256 of the exact request body bytes.

    ``None`` and an empty body both yield :data:`EMPTY_BODY_SHA256`, the digest of
    zero bytes. A ``str`` is UTF-8 encoded with no Unicode normalization.

    Raises:
        SigningError: If ``body`` is a stream, iterator, or any other type whose
            bytes are not available up front — such a body cannot be signed.
    """
    if body is None:
        return EMPTY_BODY_SHA256
    if isinstance(body, str):
        payload = body.encode("utf-8")
    elif isinstance(body, (bytes, bytearray, memoryview)):
        payload = bytes(body)
    else:
        raise SigningError(
            "request body must be bytes, str, or None; a streaming or non-seekable "
            f"body cannot be signed (got {type(body).__name__}). Read it into bytes "
            "first and send those same bytes."
        )
    return hashlib.sha256(payload).hexdigest()


def canonical_audience(audience: str) -> str:
    """Return field 2: the deployment's configured audience, validated.

    **A deployment-level constant, server-assigned, and NEVER derived from the
    request**. The grammar is
    ``\\A[A-Za-z0-9._:-]{1,64}\\Z``, which deliberately excludes ``/`` and ``\\n``.

    ⛔ **It is not a hostname**, and nothing in this package will turn one into an
    audience for you. The raw ``Host`` header is unstable across proxies, default
    ports, case, trailing dots and IDNA, and a client-supplied value the server
    trusts is not a control. There is structurally no authority reachable from
    inside this package to derive it from: :func:`split_target` and
    :func:`canonical_path` reject absolute-form targets outright, so the only
    inputs here are an origin-form path and a query.

    The value is **not** normalised — case is significant. Two deployments that
    configure ``Perceivo-API-Prod`` and ``perceivo-api-prod`` are two audiences,
    and every signed request between them fails closed, which is the correct
    direction.

    Raises:
        SigningError: If the audience is absent, not a string, or outside the
            grammar. A newline is **rejected, never escaped**.
    """
    if audience is None:
        raise SigningError(
            "audience is required and has no default; it is the deployment constant "
            "the verifier binds against (e.g. 'perceivo-api-prod'). It must never be "
            "derived from a URL or a Host header."
        )
    if not isinstance(audience, str):
        raise SigningError(f"audience must be str, got {type(audience).__name__}")
    if not _AUDIENCE_RE.match(audience):
        raise SigningError(
            "audience must be 1-64 characters from [A-Za-z0-9._:-]; it is an opaque "
            "configured label such as 'perceivo-api-prod', never a hostname or a URL"
        )
    return audience


def canonical_key_id(key_id: str | uuid.UUID) -> str:
    """Return field 3: ``api_keys.id`` as a **lowercase, hyphenated** UUID string.

    The normative form is RFC 4122 8-4-4-4-12, e.g.
    ``0f8fad5b-d9cb-469f-a165-70867728950e``. Not uppercase,
    not braced, not the 32-character unhyphenated form, and **not the key prefix**
    — the prefix is derived from the organization id, so every key in an
    organization shares it.

    ⭐ **Normalisation by construction, and rejection as the failure mode.** The
    value is parsed with :class:`uuid.UUID` and re-rendered with ``str()``, which
    is the one operation that produces exactly the normative spelling. So a caller
    who holds ``0F8FAD5B-D9CB-469F-A165-70867728950E``, ``{0f8fad5b-...}`` or the
    unhyphenated form gets the **same** signature as one who holds the canonical
    spelling, and a caller who holds something that is not a UUID at all gets a
    :class:`SigningError` at sign time. Those are the only two outcomes: this
    function cannot emit a silently different signature, which is the single most
    likely divergence between two implementations of this format.
    """
    if isinstance(key_id, uuid.UUID):
        return str(key_id)
    if not isinstance(key_id, str) or not key_id:
        raise SigningError("key_id must be a non-empty string")
    try:
        parsed = uuid.UUID(key_id)
    except (ValueError, AttributeError, TypeError):
        raise SigningError(
            "key_id must be the api_keys.id UUID, in any spelling uuid.UUID() accepts; "
            "it is normalised to the lowercase hyphenated 8-4-4-4-12 form before it is "
            "signed. It is NOT the key prefix, which every key in an organization shares."
        ) from None
    return str(parsed)


def _canonical_method(method: str) -> str:
    if not isinstance(method, str):
        raise SigningError(f"method must be str, got {type(method).__name__}")
    if not _METHOD_RE.match(method):
        raise SigningError(
            f"method must be uppercase ASCII letters, got {method!r}. It is signed "
            "exactly as it appears on the request line and is never normalized — "
            "uppercase it before calling, so what you sign is what you send."
        )
    return method


def _canonical_timestamp(timestamp: int | str) -> str:
    if isinstance(timestamp, (bool, float)):
        raise SigningError("timestamp must be Unix seconds as an int or a digit string")
    text = str(timestamp) if isinstance(timestamp, int) else timestamp
    if not isinstance(text, str) or not _TIMESTAMP_RE.match(text):
        raise SigningError(
            "timestamp must be base-10 Unix seconds with no sign, fractional part, "
            f"or leading zero, got {timestamp!r}"
        )
    return text


def _canonical_nonce(nonce: str) -> str:
    if not isinstance(nonce, str) or not _NONCE_RE.match(nonce):
        raise SigningError(
            "nonce must be 8-128 characters from [A-Za-z0-9_-]; generate it with "
            "generate_nonce() so it comes from a CSPRNG"
        )
    return nonce


def generate_nonce(n_bytes: int = 24) -> str:
    """Return a fresh CSPRNG nonce in ``[A-Za-z0-9_-]``.

    24 bytes of entropy renders as 32 characters, inside the 8–128 range the
    server accepts. A nonce is single-use: generate a new one for **every**
    attempt, including retries.
    """
    if n_bytes < 6:
        raise SigningError("nonce needs at least 6 bytes of entropy")
    return secrets.token_urlsafe(n_bytes)


def build_canonical_request(
    *,
    audience: str,
    key_id: str | uuid.UUID,
    method: str,
    raw_path: bytes | str,
    query_string: bytes | str | None,
    body: BodyType,
    content_type: ContentTypeType,
    organization: OrganizationType,
    timestamp: int | str,
    nonce: str,
) -> str:
    """Build the **eleven-field** canonical request string (wire format v1 revision 3)::

        perceivo-hmac-v1\\n{audience}\\n{key_id}\\n{METHOD}\\n{canonical_path}\\n
        {query}\\n{body_sha256_hex}\\n{content_type}\\n{organization}\\n{timestamp}\\n
        {nonce}

    Eleven fields, **exactly ten** ``\\n`` separators, no trailing newline. The
    string is UTF-8 encoded exactly once before it reaches HMAC-SHA256.

    ``raw_path`` must already have the query string removed — use
    :func:`split_target` if you have a combined target. It is passed through
    :func:`canonical_path`, so field 5 is the **BASE-normalised** path, not the
    bytes handed in.

    ⭐ **Every argument is keyword-only and required, including ``audience``,
    ``content_type`` and ``organization``.** None has a default and none is
    derivable from anything else here, so omitting one is a ``TypeError`` at the
    call site rather than a signature that is silently computed over the wrong
    string. Pass ``content_type=None`` or ``organization=None`` to mean "the
    request carries no such header"; that is a statement, not an omission.

    ⛔ **``organization`` in particular must not acquire a default.** A default
    ``""`` compares equal on both sides of every comparison the field exists to
    make, so it would silently unbind field 9 while every test stayed green — the
    same failure mode an empty audience produces, arriving through the convenience
    that was meant to make the field easy to adopt.

    ⭐ **The nonce is last, and that is load-bearing.** The nonce charset can be a
    hygiene constraint rather than an injection defence *because there is no
    field after it for an injected ``\\n`` to forge a boundary into*. Any change
    that **appends** a field after the nonce must revisit that argument. ⛔ Do
    not reorder these fields to put a new one at the end.

    ⭐ **Field 9 follows field 8 deliberately.** ``content_type`` holds wire
    position 8, so the five *unsignable* golden vectors, which record
    ``"field_number": 8`` for the field 8 charset rule, name the right field.
    """
    return "\n".join(
        (
            CANONICAL_DOMAIN,
            canonical_audience(audience),
            canonical_key_id(key_id),
            _canonical_method(method),
            canonical_path(raw_path),
            canonical_query(query_string),
            body_sha256_hex(body),
            canonical_content_type(content_type),
            canonical_organization(organization),
            _canonical_timestamp(timestamp),
            _canonical_nonce(nonce),
        )
    )
