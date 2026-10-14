"""Wire-format constants for HMAC request signing.

Values only — no logic and no imports, so both halves of the package can depend on
this module without a cycle. Changing anything here changes the wire format and
invalidates every published golden vector.
"""

from __future__ import annotations

#: Signature scheme version. Versioned so the algorithm can be rotated without a
#: breaking change: a future server accepts ``v1=`` and ``v2=`` concurrently.
#: This is also why the canonical string carries no ``host`` field — the version
#: prefix, not an extra field, is the forward-compatibility mechanism.
SIGNATURE_VERSION = "v1"
SIGNATURE_PREFIX = f"{SIGNATURE_VERSION}="

#: Field 1 of the canonical string: a **constant**, never configurable.
#: Domain separation — the same secret can no longer produce a signature valid for
#: some other HMAC use on this platform, and no other HMAC use can produce one
#: valid here. Changing this string is changing the wire format.
CANONICAL_DOMAIN = "perceivo-hmac-v1"

#: Eleven fields, therefore exactly ten ``\n`` separators and no trailing newline
#: (wire format v1, revision 3). The verifier's ``split("\n")`` count check is
#: what makes every field position meaningful: a wrong count must deny, never
#: index blindly. It is also what catches a ``\n`` smuggled into a field — an
#: injected newline yields TWELVE fields, not a forged boundary — and it is what
#: refuses a ten-field string that omits field 9.
#:
#: ⛔ **Fixed arity, never variable.** Field 9 is present even when the request
#: carries no ``Perceivo-Organization`` header; it is the empty string there. A
#: form that omitted the field when the header is absent would let an injected
#: newline produce a *legal* field count, which is the one structural
#: anti-injection defence this format has.
CANONICAL_FIELD_COUNT = 11

#: Field 2 grammar. Deliberately excludes ``/`` and ``\n``.
CANONICAL_AUDIENCE_PATTERN = r"\A[A-Za-z0-9._:-]{1,64}\Z"

#: Field 9 grammar: the lowercase hyphenated RFC 4122
#: 8-4-4-4-12 UUID, **or the empty string**.
#:
#: ⛔ The empty alternative is deliberate and is not an oversight to tighten. A
#: tenant principal sends no organization header at all, and on the ``/v1/system/``
#: surface the operation's target is in the path rather than the header, so empty
#: is the COMMON case. This is the same trap the ``Content-Type`` charset records:
#: a ``+``-shaped rule there would turn every unsigned-body GET into an unsignable
#: request.
#:
#: ⛔ **Lowercase only, and no normalisation** — deliberately diverging from
#: :func:`~perceivo._internal.signing.canonical.canonical_key_id`, which normalises.
#: ``key_id`` is derived from a ``uuid.UUID`` the server already resolved, so
#: normalising it is total and safe. Field 9 is raw client text, and admitting a
#: second spelling would require the value that is *signed* and the value that is
#: *bound* to survive an identical transform in three independent implementations.
CANONICAL_ORGANIZATION_PATTERN = (
    r"\A(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?\Z"
)

#: The deployment audiences assigned by configuration.
#: ⛔ These are labels, **not hostnames**. If the value looked like a hostname
#: someone would eventually derive it from the ``Host`` header, which is exactly
#: the instability the field exists to avoid.
AUDIENCE_PRODUCTION = "perceivo-api-prod"
AUDIENCE_LOCAL = "perceivo-api-local"

HEADER_KEY_ID = "X-Perceivo-Key-Id"
HEADER_TIMESTAMP = "X-Perceivo-Timestamp"
HEADER_NONCE = "X-Perceivo-Nonce"
HEADER_SIGNATURE = "X-Perceivo-Signature"

#: Canonical field 9's source header.
#:
#: ⛔ **It is NOT an ``X-Perceivo-*`` signature header and must not be added to
#: :data:`~perceivo._internal.signing.signer.SignedRequest.headers`.** The four above
#: are minted BY the signer; this one is a header the CALLER already sends, and
#: the signer's job is to commit to the value the request carries rather than to
#: supply one. A signer that emitted it would let a caller sign an organization
#: they never sent.
HEADER_ORGANIZATION = "Perceivo-Organization"

#: SHA-256 of zero bytes — the body field for a GET/DELETE or any empty body.
#: Never the digest of ``{}``, and never an empty field.
EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

#: The server rejects a signed request whose timestamp is further than this from
#: its own clock, in either direction.
MAX_CLOCK_SKEW_SECONDS = 300

ENV_KEY_ID = "PERCEIVO_KEY_ID"
#: ⭐ This variable holds the secret **as it was disclosed** — 64 lowercase hex
#: characters — because that is the only spelling a shell can carry, and it is
#: what an operator pastes. It is *not* the HMAC key; the key is the 32 bytes it
#: decodes to. :func:`perceivo._internal.signing.signer._secret_bytes` performs that
#: decode, so an export of the disclosed value is correct as-is. A trailing
#: newline from ``$(cat secret.txt)`` is tolerated.
ENV_SIGNING_SECRET = "PERCEIVO_SIGNING_SECRET"
#: ⛔ There is deliberately **no** environment variable for the audience. Field 2
#: is explicit configuration: the caller names it at the call site, and it is never
#: derived from a base URL, a ``Host`` header, or any ambient environment signal.
#: An env var would be one more place a deployment could grow an audience by
#: accident, and "the value was already in the environment" is exactly how a client
#: ends up signing for a deployment nobody chose. See :mod:`perceivo._internal.signing`.

BodyType = bytes | bytearray | memoryview | str | None
#: Two spellings with two meanings, and the difference is load-bearing. A ``str``
#: of 64 hex characters is the **disclosure** and is decoded to its 32 bytes; any
#: other ``str`` is keyed on its own UTF-8 bytes; ``bytes`` is the explicit,
#: unambiguous form and is used verbatim. ``bytes`` is therefore the escape hatch
#: for the one collision that cannot be resolved by inspection: a genuine
#: 64-character passphrase drawn entirely from the hex alphabet, which must be
#: passed as ``passphrase.encode()`` to be keyed as text.
SecretType = str | bytes
#: ``None`` means the request carries no ``Content-Type`` header at all. A list is
#: what an HTTP library hands back for a repeated header, and a repeated
#: ``Content-Type`` is unsignable.
ContentTypeType = str | bytes | list[str] | tuple[str, ...] | None
#: ``None`` means the request carries no ``Perceivo-Organization`` header at all.
#: A list is what an HTTP library hands back for a repeated header, and a repeated
#: ``Perceivo-Organization`` is unsignable — for a sharper reason than
#: the ``Content-Type`` case: picking one of two values means the verifier and the
#: *tenancy decision* can pick differently, which is the very confusion field 9
#: exists to close.
OrganizationType = str | bytes | list[str] | tuple[str, ...] | None
