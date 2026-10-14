"""Canonical fields 8 and 9 — the two the request HEADERS supply.

This module holds the builders for those two fields, and they belong together
rather than merely fitting together. They are the only fields
whose value is a header the caller sends rather than something the signer derives,
so they share one contract shape and one set of traps:

* **verbatim** — never normalised, never case-folded, never repaired;
* **absent is the empty string**, the same convention field 6 uses for an absent
  query, and the ``*``-shaped grammar that permits it is deliberate in both;
* **a repeated header is REJECTED, never resolved** — two values is an ambiguity,
  and a signer that picks one may not pick the one the server picks;
* **an inadmissible byte is REJECTED, never escaped** — escaping keeps the arity
  right and produces a string no other implementation would reproduce.

⛔ **Where they differ is substantive, and it is not a symmetry to tidy up.** Field 8 needs no binding at the verifier: the edge builds it
from the header it received and the signature proves the client sent that value,
and there is nothing at the verifier to compare it against. Field 9 **selects the
tenant the authorization decision runs against**, so the value that entered the
canonical string and the value the tenancy decision uses must be proved to be the
same value — after the MAC, never before it. A field 9 that is signed but never
re-derived buys nothing and reads as present, which is worse than absent.

They also differ in charset, and that is deliberate too: field 8 admits any ASCII
printable byte, which makes it the only field carrying attacker-influenced text
with no shape of its own. Field 9 is given the UUID shape precisely so that
the format does not admit a **second** such field.

Re-exported from :mod:`~perceivo._internal.signing.canonical`, which remains the
import site for every caller.
"""

from __future__ import annotations

import re

from perceivo._internal.signing._constants import (
    CANONICAL_ORGANIZATION_PATTERN,
    ContentTypeType,
    OrganizationType,
)
from perceivo._internal.signing._errors import SigningError

__all__ = ["canonical_content_type", "canonical_organization"]

#: The normative charset for canonical field 8: ASCII printable only. This is the
#: SAME accepted set the platform's verifier applies, and matching the *accepted
#: set* is the point — not matching its decode. That verifier reads the raw
#: header bytes as latin-1 while this signer
#: decodes them as UTF-8, and on ASCII printable the two decodes are the identity
#: function, so once neither side can admit anything else the two provably agree
#: without either having to concede a charset to the other.
#:
#: ⛔ Empty is ALLOWED (`*`, not `+`): an absent Content-Type is the empty string by
#: the same convention field 6 uses for an absent query, and a `+` here would turn
#: every unsigned-body GET into an unsignable request (v01 and v29 are exactly that
#: shape).
_CONTENT_TYPE_CHARSET = re.compile(r"[\x20-\x7e]*")

#: The normative charset for canonical field 9: the lowercase hyphenated
#: RFC 4122 UUID, or the empty string. Nothing else.
#:
#: ⭐ **This is a NARROWER rule than field 8's, and the narrowing is what keeps
#: field 8 the only field carrying unconstrained attacker-influenced text.** Field
#: 9 gets a charset of its own precisely so that the format does not admit a
#: *second* such field. A newline is refused by this
#: same rule, and is REJECTED rather than escaped — an injected one must yield
#: twelve fields and a denial, not a forged boundary.
_ORGANIZATION_CHARSET = re.compile(CANONICAL_ORGANIZATION_PATTERN)


def canonical_content_type(content_type: ContentTypeType) -> str:
    """Return field 8: the ``Content-Type`` request header **verbatim**.

    An absent header yields the empty string — the same convention field 6 uses
    for an absent query, and field 9 uses for an absent ``Perceivo-Organization``.
    The separators remain either way, so the canonical string always has eleven
    fields.

    ⛔ **Not normalised. Not case-folded. Parameters NOT stripped.** Signing a
    "canonical media type" would look like a fix and leave the finding open: the
    attack re-frames *identical signed body bytes* by altering the multipart
    ``boundary`` parameter, and on a multipart route the parser, the boundary and
    the charset are selected from this header alone. A canonical media type drops
    exactly the parameter that carries the attack. ``charset`` is the second
    variant by the same mechanism.

    ⚠️ **A repeated header is rejected, not resolved.** Two values is an
    ambiguity, and picking one means the verifier and the parser can pick
    differently — which is the very confusion this field exists to close. Pass the
    result of a ``get_list``-style accessor so a duplicate is visible here rather
    than silently joined into one value by a ``get``.

    ⚠️ **Sign after the request is final.** HTTP client libraries generate the
    multipart boundary themselves, so a signature computed before the client sets
    ``Content-Type`` commits to a value the request never carries, and every
    signed upload 401s. Build the request first, then sign it.

    ⛔ **ASCII printable (``0x20``–``0x7E``) only, and a byte outside it is
    REJECTED here rather than signed.** This is narrower than RFC 7231, which
    admits ``obs-text`` inside a quoted-string, and the narrowing is deliberate:
    the platform's verifier refuses the same set at the edge, so a
    signature computed over a field 8 carrying ``é`` commits to a value that
    verifier will never accept. Without this check the SDK signs happily and the
    caller gets a bare ``invalid_signature`` on every such request forever, with
    nothing to diagnose it by — and the SDK is the only participant still holding
    the caller's own input, so it is the only place that can say *why*. The rule
    **subsumes** the newline case rather than sitting beside it: ``\\n`` is
    ``0x0A``, outside the range, and is still rejected and never escaped.

    Raises:
        SigningError: If the header is repeated, is not decodable text, or
            contains any byte outside ASCII printable ``0x20``–``0x7E`` — which
            includes ``\\n`` (**rejected, never escaped**).
    """
    if content_type is None:
        return ""
    if isinstance(content_type, (list, tuple)):
        if not content_type:
            return ""
        if len(content_type) > 1:
            raise SigningError(
                "the request carries more than one Content-Type header, which cannot be "
                "signed: two values is an ambiguity, and a signer that picks one may not "
                "pick the same one the origin's body parser picks. Send exactly one."
            )
        content_type = content_type[0]
    if isinstance(content_type, (bytes, bytearray, memoryview)):
        try:
            content_type = bytes(content_type).decode("utf-8")
        except UnicodeDecodeError:
            raise SigningError("Content-Type must be UTF-8 decodable to be signed") from None
    if not isinstance(content_type, str):
        raise SigningError(
            f"Content-Type must be str, bytes or None, got {type(content_type).__name__}"
        )
    if not _CONTENT_TYPE_CHARSET.fullmatch(content_type):
        raise SigningError(
            "Content-Type must be ASCII printable (0x20-0x7E) to be signed; the "
            "byte(s) outside that range cannot be placed in canonical field 8. It "
            "is REJECTED, never escaped or transliterated. This is narrower than "
            "RFC 7231, which admits obs-text in a quoted-string, and the narrowing "
            "is what lets a latin-1 verifier and a UTF-8 signer agree on the field. "
            "A newline is refused by this same rule."
        )
    return content_type


def canonical_organization(organization: OrganizationType) -> str:
    """Return field 9: the ``Perceivo-Organization`` request header **verbatim**.

    An absent header yields the empty
    string — the same convention field 6 uses for an absent query and field 8 for
    an absent ``Content-Type``. ⛔ **The field is present either way**; the string
    has the same field count for a request that sends no header. See
    :data:`~perceivo._internal.signing._constants.CANONICAL_FIELD_COUNT`.

    ⭐ **What this field buys, and why it is not field 8 again.** The organization
    header selects *the tenant whose data the request reads and writes*. Without it
    in the signature, an operator's captured signature for organization A is
    re-presented with a header naming organization B and verifies, because nothing
    in the signature commits to the tenant. The audit trail makes that worse rather
    than better: operator actions are written into the **tenant's own** event feed,
    so an unsigned header produces a customer-facing record that faithfully names
    the *victim*.

    ⛔ **Rejected, never escaped, never repaired, and never normalised.** The value
    must be the lowercase hyphenated RFC 4122 8-4-4-4-12 form or the empty string.
    An uppercase, braced or unhyphenated UUID, a non-UUID value, and a value
    carrying ``\\n`` are all **unsignable**.

    ⚠️ **This deliberately diverges from :func:`canonical_key_id`, which
    normalises.** The divergence is the point rather than an inconsistency:
    ``key_id`` is derived from a ``uuid.UUID`` the *server* already resolved, so
    re-rendering it is total and safe. Field 9 is **raw client text** that must
    survive byte-identically through a signer, a verifier and a tenancy decision in
    three independent implementations — and admitting a second spelling would
    require all three to agree on an identical transform. Refusing everything but
    the canonical spelling removes that question instead of answering it, which is
    the same doctrine that produced field 8's ASCII-printable ruling.

    ⚠️ **A repeated header is rejected, not resolved** — and for a sharper reason
    than field 8's. Two values is an ambiguity, and picking one means the verifier
    and the *tenancy decision* can pick differently, which is exactly the confusion
    this field exists to close. Pass the result of a ``get_list``-style accessor so
    a duplicate is visible here rather than silently joined by a ``get``.

    ⚠️ **Sign the value you send.** The verifier re-derives field 9 from the signed
    string *after* the MAC comparison and refuses any disagreement with the header
    it received, so a request that signs one organization and sends another fails
    with a perfectly correct MAC and no other symptom.

    Raises:
        SigningError: If the header is repeated, is not decodable text, or is
            neither the empty string nor a lowercase hyphenated UUID — which
            includes ``\\n`` (**rejected, never escaped**).
    """
    if organization is None:
        return ""
    if isinstance(organization, (list, tuple)):
        if not organization:
            return ""
        if len(organization) > 1:
            raise SigningError(
                "the request carries more than one Perceivo-Organization header, which "
                "cannot be signed: two values is an ambiguity, and a signer that picks "
                "one may not pick the same one the server's tenancy decision picks. "
                "Send exactly one."
            )
        organization = organization[0]
    if isinstance(organization, (bytes, bytearray, memoryview)):
        try:
            organization = bytes(organization).decode("utf-8")
        except UnicodeDecodeError:
            raise SigningError(
                "Perceivo-Organization must be UTF-8 decodable to be signed"
            ) from None
    if not isinstance(organization, str):
        raise SigningError(
            f"Perceivo-Organization must be str, bytes or None, got {type(organization).__name__}"
        )
    if not _ORGANIZATION_CHARSET.fullmatch(organization):
        raise SigningError(
            "Perceivo-Organization must be a LOWERCASE hyphenated 8-4-4-4-12 UUID, or "
            "absent. It is REJECTED, never normalised, repaired or escaped: uppercase, "
            "braced and unhyphenated spellings are refused here rather than folded, "
            "because the value that is signed and the value the server binds must be "
            "the same bytes in three independent implementations. Pass "
            "organization=None to mean 'this request carries no such header'."
        )
    return organization
