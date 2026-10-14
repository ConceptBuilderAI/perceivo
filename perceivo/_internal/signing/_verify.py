"""Verification: the MAC check, and the bound check a server is written against.

This module holds the verification side of the signer. The seam is the direction of the operation. ``signer`` PRODUCES signature
material for a request it is handed; this module CONSUMES a canonical string and
a signature somebody else produced, so every function here is a predicate over
attacker-influenced input and every one of them is total by contract -- malformed
input is ``False``, never an exception. Keeping them together keeps that contract,
and the two totality helpers that enforce it, in one place to read.

``compute_signature`` sits on this side of the seam because it is the shared
primitive: ``verify_signature`` is *defined* as computing it and comparing in
constant time, and separating the two would put the definition in one module and
its only non-signing caller in another.

Everything here is re-exported from ``perceivo._internal.signing.signer`` and from
``perceivo._internal.signing``; both remain supported import paths and hand back the
SAME function objects.

The signing secret reaches this module (``compute_signature`` keys the HMAC with
it) and is held to the same rules as in ``signer``: never logged, never
interpolated into an exception message, and compared only with
``hmac.compare_digest``.
"""

from __future__ import annotations

import hashlib
import hmac

from perceivo._internal.signing._constants import (
    CANONICAL_DOMAIN,
    CANONICAL_FIELD_COUNT,
    SIGNATURE_PREFIX,
    SecretType,
)
from perceivo._internal.signing._secret import (
    # Named ``_secret_bytes`` here for the same reason ``signer`` does: `_constants`
    # cites ``perceivo._internal.signing.signer._secret_bytes`` and the bodies below
    # call it under that spelling. The SAME function object ``_secret`` defines.
    secret_to_key_bytes as _secret_bytes,
)
from perceivo._internal.signing.canonical import (
    SigningError,
    canonical_audience,
    canonical_key_id,
)

__all__ = [
    "compute_signature",
    "verify_signature",
    "verify_signed_request",
]


def _is_utf8_encodable(value: object) -> bool:
    """Return ``True`` if ``value`` is a ``str`` that UTF-8 will actually encode.

    ⛔ **Not every ``str`` encodes.** A lone surrogate — ``"\\ud800"`` — is an
    ordinary ``str`` that ``str.encode("utf-8")`` refuses with
    ``UnicodeEncodeError``, and Python's own JSON decoder mints one from the
    six-character escape ``\\ud800`` in any request body. So a lone surrogate is
    reachable from the wire, arrives inside the canonical request a verifier is
    handed, and raises at the ``.encode("utf-8")`` inside
    :func:`compute_signature` — an unhandled 500 on the authentication path where
    a refusal was required.

    Guarding here rather than catching around ``compute_signature`` keeps the
    check in front of every path that encodes, including the one
    :func:`verify_signed_request` reaches after its own field comparisons.
    """
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _digest_equal(received: str, expected: str) -> bool:
    """Constant-time equality that is **total over ``str``**.

    ⛔ ``hmac.compare_digest`` raises ``TypeError`` when either ``str`` operand
    carries a non-ASCII character. The ``received`` operand here is a canonical
    field sliced straight out of attacker-supplied bytes, so the raise is
    reachable and turns a refusal into a 500.

    Returning ``False`` is the **exact** answer rather than an approximation of
    one: every ``expected`` value this module compares against is ASCII by
    construction — the constant :data:`CANONICAL_DOMAIN`, an audience from the
    ``[A-Za-z0-9._:-]`` grammar, or a lowercase hyphenated UUID — so a
    ``received`` value carrying non-ASCII cannot be equal to it under any
    encoding. Nothing legitimate is excluded.

    ⚠️ The ``isascii()`` short-circuit is deliberately not treated as a timing
    leak: it discloses only that the *caller's own input* contained a byte
    outside ASCII, which the caller already knows, and no secret is reachable
    from this branch. The digest comparison itself stays constant-time.

    ⚠️ Totality is claimed over ``received``, which is attacker-supplied, and
    **not** over ``expected``, which is the verifier's own configured audience or
    key id. A non-``str`` ``expected`` raises, exactly as a malformed ``secret``
    does in :func:`verify_signature`: a server misconfigured at that end should
    say so, not silently refuse every request it is sent.
    """
    if not received.isascii() or not expected.isascii():
        return False
    return hmac.compare_digest(received, expected)


def compute_signature(canonical_request: str, secret: SecretType) -> str:
    """Return the ``X-Perceivo-Signature`` value: ``v1=<64 lowercase hex chars>``.

    The ``v1=`` prefix is **required** — the server rejects a correct HMAC
    presented without it, and rejects the same hex under ``v2=``.
    """
    if not isinstance(canonical_request, str):
        raise SigningError("canonical_request must be str")
    digest = hmac.new(
        _secret_bytes(secret), canonical_request.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(signature: str, canonical_request: str, secret: SecretType) -> bool:
    """Constant-time check of a ``v1=`` signature against a canonical request.

    Provided for the customer self-check ("sign a published vector, compare") and
    as the reference the server verifier is written against. Compares with
    ``hmac.compare_digest``, never ``==``, and never raises on a malformed
    signature — a malformed signature is simply invalid.

    ⛔ **This is a MAC check and nothing else.** It does not bind the domain, the
    audience or the key id — see :func:`verify_signed_request` for those — and it
    does not enforce timestamp freshness or nonce single-use. A server that
    authenticates with this function alone accepts a captured request replayed
    forever.

    **Both guards are load-bearing, not defensive clutter.** The function is total
    over its request-derived inputs because it is a *predicate over
    attacker-influenced input*: a verifier modelled on it that raises instead of
    returning ``False`` turns a malformed header into a 500 rather than a 401.

    * ``signature`` — ``hmac.compare_digest`` raises ``TypeError`` on a ``str``
      containing non-ASCII, so ``v1=<non-ascii>`` would reach it and raise. What
      this module produces is always ``v1=`` + lowercase hex.
    * ``canonical_request`` — a lone surrogate is a ``str`` that UTF-8 refuses to
      encode, so it raises ``UnicodeEncodeError`` inside
      :func:`compute_signature`. Field 7 is a hex digest and every other field is
      constrained to ASCII by its own grammar, so no request this package can
      sign is excluded by the check.

    ⚠️ ``secret`` is deliberately **not** guarded into a ``False``: it is the
    deployment's own key material, never attacker-supplied, and a malformed one
    still raises :class:`SigningError` so a misconfigured server says so instead
    of silently refusing every request.
    """
    if not _is_utf8_encodable(canonical_request):
        return False
    if (
        not isinstance(signature, str)
        or not signature.isascii()
        or not signature.startswith(SIGNATURE_PREFIX)
    ):
        return False
    expected = compute_signature(canonical_request, secret)
    return hmac.compare_digest(expected, signature)


def verify_signed_request(
    *,
    signature: str,
    canonical_request: str,
    secret: SecretType,
    audience: str,
    key_id: str,
) -> bool:
    """Verify a signature **and** the bindings the format exists to create.

    ⛔⛔ **THIS IS NOT A COMPLETE SERVER VERIFIER. DO NOT SHIP IT AS ONE.** ⛔⛔

    **If you are copying this function into a server, you must add three checks
    it does not perform.** ⛔ Field numbers are 1-based, so the number→index
    conversion is ``fields[n - 1]``: the timestamp is ``fields[9]``, and reading
    ``fields[8]`` — the organization — instead makes ``int()`` on a uuid or on
    the empty string raise on the authentication path.

    1. **Timestamp freshness.** Reject the request unless
       ``|now - field 10| <= 300`` seconds (:data:`MAX_CLOCK_SKEW_SECONDS`).
    2. **Nonce single-use.** Reject the request unless field 11 has never been
       seen before, recording it for at least the freshness window.
    3. **Organization binding.** Re-derive field 9
       from the signed string and compare it with the ``Perceivo-Organization``
       header **the edge actually received**; refuse any disagreement. ⛔ This
       comparison runs **AFTER** ``compare_digest`` and never in a prelude check:
       both sides of it are client-asserted, so a prelude that refused on it
       would answer before the mac had proven the string came from the key
       holder at all. This function has no request to read a header from, so it
       cannot make the comparison — which is why it **accepts**
       ``n07-organization-metadata-unbound``, correctly and by design. A server
       that copies only checks 1 and 2 accepts ``n07`` in production while every
       published vector still passes, and a captured signature stays re-aimable
       at another tenant.

    Fields 9, 10 and 11 are *present in the string this function checks and are
    bound to nothing by it*. That is the same defect the table's negative vectors
    exist to punish for fields 1-3: a field the verifier never compares against
    anything buys nothing, because the attacker sets it to whatever makes the mac
    verify. **A server built on this function alone gains domain, audience and
    key-id binding, has no replay protection at all and no tenant binding** — a
    captured request replays forever, unchanged, at whatever organization the
    attacker names, and every check below returns ``True``.

    The omission is deliberate and is not a bug to file: this package has no
    clock it trusts, no shared nonce store and no request headers, and inventing
    any of them here would produce a control that looks like replay protection in
    a single process and is not one behind more than one replica. The three
    checks belong to the server, which owns all three.

    :func:`verify_signature` is a pure MAC check, and a MAC check alone accepts
    every one of the attacks those fields exist to close: a canonical string
    naming a different audience, or a different key id, or a different domain, is
    self-consistent and its own HMAC is perfectly valid. What makes those fields
    defences is that a verifier **binds** them against values it holds itself.
    This is the reference for that verifier:

    1. the string splits into exactly :data:`CANONICAL_FIELD_COUNT` fields;
    2. field 1 equals :data:`CANONICAL_DOMAIN` exactly;
    3. field 2 equals the audience **this deployment is configured with**;
    4. field 3 equals the key id whose secret was fetched — which on a server is
       the id resolved from the bearer credential, **never** a caller-supplied
       header.

    ⛔ **All four run BEFORE ``compare_digest``, and all four return the same
    ``False``.** A distinct outcome per check is an oracle: it tells an attacker
    which of the four they got wrong, and field 2 in particular is server-assigned,
    so naming it back to the caller leaks configuration. There is deliberately no
    "reason" in this signature.

    ⚠️ Field 8 (``content_type``) needs no binding here and is not checked: there
    is nothing at the verifier to compare it against. The edge builds it from the
    header it received, and the signature is what proves the client sent the same
    value.

    Total by contract, exactly like :func:`verify_signature` — malformed input is
    invalid, never an exception. ⛔ **That totality has to hold on the paths
    BEFORE the mac check, not only inside it.** Fields 1-3 reach
    ``hmac.compare_digest`` here, and ``compare_digest`` raises ``TypeError`` on
    a ``str`` carrying non-ASCII; a guard living in :func:`verify_signature`
    protects only the ``signature`` argument and runs last, so it is bypassed
    entirely. Fields 1, 2 and 3 are built by the edge from the request it
    received, and a lone surrogate anywhere in ``canonical_request`` comes from
    any JSON body — every one of them is attacker-reachable. They are guarded by
    :func:`_digest_equal` and :func:`_is_utf8_encodable`, which return the same
    ``False`` every other refusal returns: a distinct exception type or message
    per check would be exactly the oracle the paragraph above forbids.
    """
    # ⚠️ **Honest accounting of what this line does.** Its `isinstance` half is
    # load-bearing: without it a non-`str` reaches `.split("\n")` and raises
    # `AttributeError`. Its ENCODABILITY half is defence in depth and is currently
    # not observable — mutating it back to a bare `isinstance` check fails no test,
    # because a lone surrogate is caught downstream either way: in fields 1-3 by
    # `_digest_equal`'s `isascii()`, and anywhere else by `verify_signature`'s own
    # guard, which runs before it touches the secret. It is kept because it makes
    # THIS function total on its own terms rather than by a reader tracing two
    # helpers, and because the two total helpers are each independently proven.
    if not _is_utf8_encodable(canonical_request):
        return False
    try:
        expected_audience = canonical_audience(audience)
        expected_key_id = canonical_key_id(key_id)
    except SigningError:
        # A deployment misconfigured at this end fails closed, and says no more
        # about it to the caller than a bad signature would.
        return False

    fields = canonical_request.split("\n")
    if len(fields) != CANONICAL_FIELD_COUNT:
        return False
    if not _digest_equal(fields[0], CANONICAL_DOMAIN):
        return False
    if not _digest_equal(fields[1], expected_audience):
        return False
    if not _digest_equal(fields[2], expected_key_id):
        return False
    return verify_signature(signature, canonical_request, secret)
