"""Client-side HMAC-SHA256 request signing for the Perceivo public API (``v1``).

Stdlib only — ``hmac``, ``hashlib``, ``secrets``, ``time``, ``os``, ``re``. Nothing here
performs **any I/O or network call**, and nothing here imports ``httpx``: the helper
is usable standalone, without the SDK's transport. A caller who does not want the
SDK as a dependency at all can copy the ~40-line signer in
``examples/hmac_signing.py`` instead.

Layout: :mod:`~perceivo._internal.signing.canonical` holds the pure, secret-free
canonicalization; :mod:`~perceivo._internal.signing.signer` holds everything that
touches the signing secret; :mod:`~perceivo._internal.signing._constants` holds the
wire-format values. This file is the public surface and the normative record of
the rulings below.

Specification: this signer implements **wire format v1, revision 3**. Two rulings
are easy to get wrong: **the path field is the BASE-normalised
``canonical_path``, not ``raw_path`` verbatim; the query field is VERBATIM, not
sorted.** Independent implementations must agree byte for byte.

The canonical request
=====================
Exactly **eleven** fields joined by ten ``\\n`` (0x0A) separators, with **no trailing
newline**::

    perceivo-hmac-v1\\n{audience}\\n{key_id}\\n{METHOD}\\n{canonical_path}\\n
    {query}\\n{sha256_hex(body)}\\n{content_type}\\n{organization}\\n{timestamp}\\n{nonce}

The string is encoded **UTF-8** once and passed to HMAC-SHA256 keyed on the
signing secret's **raw bytes** — the 32 bytes the server sealed, never a hex or
base64 re-encoding of them.

⭐ **The secret you were handed at enrolment is those bytes spelled as 64
lowercase hex characters**, so a caller's job is to *decode*, not to encode:
``bytes.fromhex(disclosure)``. This client does it for you — a ``str`` matching
``[0-9a-fA-F]{64}`` is decoded from hex, any other ``str`` is encoded UTF-8, and
``bytes`` is passed through verbatim. "Never a re-encoding" describes the
**key**, not the **transport**. UTF-8-encoding the disclosure builds a
**64-byte** key against the server's **32-byte** one, so every request fails with
an ordinary 401 that names no cause and leaves nothing to inspect on either side.

**Why eleven fields.** The ``v1=`` prefix is the forward-compatibility
mechanism, so a v2 is possible later — but a v2 means running two verifiers
through a migration and carrying the weaker one forever. Each field beyond the
request itself buys something specific:

* **``perceivo-hmac-v1`` — domain separation.** The same secret can no longer
  produce a signature valid for some other HMAC use on this platform, and no
  other use can produce one valid here. Cheap and unconditional. It is a
  **constant** and is never configurable.
* **``{audience}`` — cross-origin replay.** Without it, a signed request captured
  at origin A replays verbatim at any origin B that accepts the same key: same
  method, target, body, timestamp, nonce and signature — and B's nonce store is a
  *different* store, so B's replay defence never fires. See **Audience** below.
* **``{key_id}`` — identifier substitution.** The key id selects both the
  verification secret and the authorisation principal. If two keys ever hold the
  same signing secret, a request signed as key A can be presented as key B and
  verifies. Per-key secret uniqueness makes that fail today, but uniqueness is an
  *operational* property; signing the id makes it cryptographic. The residual is
  narrower than it looks — the storage layer already seals each secret with GCM
  associated data carrying the key id — but "operator seals the same plaintext
  twice under two ids" is exactly what this closes.
* **``{content_type}`` — parser confusion.** No header was signed, and
  ``content-type`` is not stripped in transit, so it reaches the origin as an
  intermediary left it. On a multipart route the parser, the **boundary** and the
  **charset** are all selected from that header alone: identical signed body bytes
  re-frame into different form fields and the signature still verifies. See
  **Content-Type** below.
* **``{organization}`` — cross-tenant replay.** Without it,
  an operator's captured signature for organization A is re-presented with a
  ``Perceivo-Organization`` header naming organization B and verifies, because
  nothing in the signature commits to the tenant. The header is admin-only and
  administrative principals are HMAC-only, so the only signature worth tampering
  with is the most privileged one on the platform; and nothing in transit
  removes the header either. The audit trail makes it worse rather than better: operator actions are
  written into the **tenant's own** event feed, so an unsigned header produces a
  customer-facing record that faithfully names the **victim** tenant. See
  **Organization** below.

⛔ **The point of ``{key_id}`` and ``{audience}`` is that they become BOUND
fields, not merely present ones.** A field that is *in* the string but never
compared against a value the verifier holds itself buys nothing at all —
:func:`verify_signed_request` is the reference for that binding.

⛔ **``{organization}`` carries the same obligation and it is the one most likely
to be got wrong.** ``{content_type}`` needs no verifier binding
because there is nothing at the verifier to compare it against. That argument
**does not carry to** ``{organization}``: the value *selects the tenant the
authorization decision runs against*, so the value that entered field 9 and the
value the tenancy decision uses must be proved to be the same value — after the
MAC, never before it, because both sides of that comparison are client-asserted.
A field 9 that is signed but never re-derived at the verifier buys **nothing** and
reads as present, which is worse than absent.

``host`` is **not** a field. The signature covers **no
authority at all** — not the ``Host`` header and not an absolute-form request
target — and carries **no scheme and no absolute URL**: the origin
observes ``http`` even for a request the client made over ``https``, so any
canonicaliser built from an absolute URL would disagree with every client and 401
every signed request. The signature commits to the **routed request**, not to the
wire bytes. The audience is what replaces the authority, and it is configured
rather than observed for exactly that reason.

Canonicalization rulings
========================
Every rule below is pinned by a test. Ambiguity here is what turns an HMAC
integration into an undebuggable 401, so nothing is left to the implementer.

**No field may contain ``\\n``, and a field that does is REJECTED, never
escaped.** Fields 1, 2, 3 are constrained by their own grammars; method is a
token, the path and query are percent-encoded, the digest is hex, the timestamp is
digits and the nonce is ``[A-Za-z0-9_-]``. ``{content_type}`` is the only field
carrying attacker-influenced text with no charset of its own, so it is the one
place the rule does real work — ``{organization}`` is given a charset of its own
(**Organization** below) precisely so the format does not admit a second such
field. Escaping would create a second spelling of the same
string; rejecting keeps the map from requests to canonical strings injective.

**Audience.** A **deployment-level constant, server-assigned, configured, and
NEVER derived from the request.** The grammar is
``\\A[A-Za-z0-9._:-]{1,64}\\Z`` — no ``/``, no ``\\n``. Production is
``perceivo-api-prod``; local dev is ``perceivo-api-local``. ⛔ **It is not a
hostname.** If it looked like one, someone would eventually derive it from
``Host`` and reintroduce exactly the instability the field exists to avoid:
proxies, default ports, case, trailing dots and IDNA all move a hostname around,
and a client-supplied value the server trusts is not a control.

This package makes the audience **hard to omit and impossible to derive**, by
construction rather than by documentation:

* it is a **required keyword-only argument** of :func:`build_canonical_request`
  and :func:`sign_request`, with no default, so omitting it is a ``TypeError`` at
  the call site rather than a signature over a guessed value;
* it is a **required field of** :class:`SigningCredentials`, so a
  :class:`RequestSigner` cannot be constructed without one and no signing path in
  the SDK can reach the wire unaudienced;
* :func:`resolve_signing_credentials` extends the existing never-half-on rule to
  it — key id and secret present with no audience **raises**. ⛔ Unlike those two
  there is **no environment fallback**: ``key_id`` and ``signing_secret`` are
  credentials and may come from ``PERCEIVO_KEY_ID`` / ``PERCEIVO_SIGNING_SECRET``,
  but the audience is *configuration* and is supplied at the call site or not at
  all. An env var would be one more ambient place an audience could appear without
  anyone choosing it. ``Client`` deliberately does not pass ``base_url`` into that
  call either;
* nothing in this package accepts a URL, an authority or a ``Host``.
  :func:`split_target` and :func:`canonical_path` reject absolute-form targets, so
  there is structurally no authority in scope to derive an audience *from*.

The value is not normalised — case is significant, and two deployments that spell
it differently simply fail closed in both directions. **Per-key audience scoping
is deliberately not implemented**: each deployment owns its own ``api_keys``
table, so a key issued by X cannot be presented at Y with a valid secret anyway.
It is the known follow-on, not an oversight.

**Key id.** ``api_keys.id`` as a **lowercase, hyphenated, RFC 4122 8-4-4-4-12**
UUID — ``0f8fad5b-d9cb-469f-a165-70867728950e``. Not uppercase, not braced, not
the 32-character unhyphenated form, and **not the key prefix** (the prefix is
derived from the organization id, so every key in an organization shares it).

The representation is normative because it is the single most likely divergence
between two implementations and it produces a signature mismatch with **no other
symptom**. So :func:`canonical_key_id` normalises rather than assumes: the value
is parsed with :class:`uuid.UUID` and re-rendered with ``str()``, which yields
exactly the normative spelling. Every accepted spelling therefore produces the
**same** signature, and anything that is not a UUID raises :class:`SigningError`
at sign time. There is no third outcome — no path emits a silently different
signature. ``X-Perceivo-Key-Id`` carries that same normalised form, so the header
and canonical field 3 are byte-identical.

**Content-Type.** The request header **verbatim**; an absent header is the empty
string, the same convention the query field uses. ⛔ **Not normalised, not
case-folded, parameters NOT stripped.** Signing a "canonical media type" is the
obvious way to avoid interoperability pain and it would leave the finding open
while looking like a fix: the attack re-frames identical signed body bytes by
altering the multipart ``boundary`` parameter, and a canonical media type drops
exactly that. ``charset`` is the second variant by the same mechanism. A repeated
header is **rejected**, not resolved — two values is an ambiguity, and a signer
that picks one may not pick the one the origin's body parser picks.

⚠️ **Sign after the request is final.** HTTP client libraries generate the
multipart boundary themselves, so a signature computed before the client sets
``Content-Type`` commits to a value the request never carries and every signed
upload 401s. The SDK transport builds the request with ``httpx`` first and signs
what came back, reading the header with ``get_list`` so a duplicate is visible
rather than joined.

**Organization.** Field 9 is the ``Perceivo-Organization`` request header
**verbatim**; an absent header is the empty string, the same convention fields 6
and 8 use. ⛔ **The field is always present** — there is no ten-field form of the
string, and a signer that omits it fails on every request. Fixed arity is not pedantry: a variable-length form would let a
``\\n`` injected into any field produce a *legal* field count, and the arity check
is the one structural anti-injection defence this format has.

⛔ **Rejected, never normalised, never repaired, never escaped.** The value must
be a **lowercase** hyphenated RFC 4122 8-4-4-4-12 UUID or the empty string; an
uppercase, braced or unhyphenated spelling raises :class:`SigningError` at signing
time. ⚠️ This **deliberately diverges from key_id**, which *is* normalised for the
caller. The divergence is the point: ``key_id`` is derived from a ``uuid.UUID``
the server already resolved, so re-rendering it is total and safe, while field 9
is raw client text that must survive byte-identically through a signer, a verifier
**and a tenancy decision** in three independent implementations. Refusing every
spelling but one removes that question instead of answering it — the same doctrine
that produced Content-Type's ASCII-printable rule — and it makes the
latin-1/UTF-8 decode question moot for this field exactly as it did for field 8.

A repeated header is **rejected**, not resolved, for a sharper reason than
Content-Type's: picking one of two values means the verifier and the *tenancy
decision* can pick differently, which is the very confusion the field exists to
close. ⚠️ **Sign the value you send** — the verifier re-derives field 9 from the
signed string *after* the MAC comparison and refuses a disagreement, so signing
one organization and sending another fails with a perfectly correct MAC.

⚠️ **The definition is unconditional — do not make it route-conditional.** The
expected *values* vary by surface (a tenant principal sends no header, a system
route carries its target in the path), but that is an expectation about traffic,
not a constraint on the format: an implementation that hard-codes the empty string
on some route class fails closed, permanently, for any client that does send one.

**Character set.** ``raw_path`` and the query string must be **printable ASCII**
(0x21–0x7E) — origin-form and already percent-encoded, exactly as an HTTP client
puts them on the request line. Non-ASCII input raises :class:`SigningError` rather
than being encoded on the caller's behalf, because the encoding an HTTP library
will actually apply is the library's decision, not this package's. This character-set
check is the **only** transformation applied to the query string.

**METHOD.** Uppercase ASCII (``^[A-Z]+$``), signed exactly as it appears on the
request line. A lowercase method is **rejected, never normalized**: silently
signing ``GET`` while the caller's HTTP library sends ``get`` produces a 401 that
cannot be debugged from either side.

**canonical_path.** Field 5 is **not** ``raw_path`` verbatim. It is ``raw_path``
— the target with the query string removed — put through the RFC 3986 §6.2.2
``BASE`` transform, which is **two** operations applied in **this order**:

1. **Percent-decode unreserved escapes only** — ``A-Z a-z 0-9 - . _ ~``. Both hex
   cases decode: ``%7e`` and ``%7E`` both become ``~``.
2. **Remove dot segments** — RFC 3986 §5.2.4, applied to the output of step 1.

**Nothing else happens.** Specifically:

* ``//`` is **not** merged — ``/a//b`` stays ``/a//b``;
* a trailing ``/`` is **not** stripped — ``/x`` and ``/x/`` remain different
  requests;
* a **reserved** escape is not decoded and its hex case is not normalized —
  ``%2F`` stays ``%2F``, ``%2f`` stays ``%2f``, and the two still sign
  differently. Decoding them would collide ``/a%2Fb`` with ``/a/b``, which are
  different resources;
* the query string is not touched by any of this (see **canonical_query**).

**The step order is load-bearing.** ``%2E`` is unreserved, so step 1 decodes it
to ``.`` and step 2 must then remove it as a dot segment: ``/a/%2E%2E/b``
canonicalizes to ``/b``. An implementation that removes dot segments *before*
decoding emits ``/a/../b`` and produces a different signature. ``%2E`` is the
one spelling that separates the two orders, which is why the vector table pins
it (``v27``).

**Why the path is normalized when the query is not.** The platform's ingress
applies exactly this transform (Envoy ``pathNormalization: BASE``) to every
request path before the origin sees it, and applies **nothing** to the query.
The transform that produces the signature base must be the same transform the
routing layer applies — so the path gets this one, and
the query gets none. Signing the path as sent instead would 401 every request
carrying ``%7e``, ``%7E``, or a ``.``/``..`` segment.

**The transform is deliberately not injective.** ``/a/%7eb`` and ``/a/~b``
are two request targets that produce one signature base: a signature commits to
an *equivalence class* of targets, not to a single target. That is safe only
because every member of the class reaches the origin as the same normalized
path — the class the signature commits to and the class the router cannot
distinguish are the same class. Contrast **canonical_query**, where collapsing a
class would be a defect, because there the router *can* distinguish the members.

(``httpx`` removes dot segments itself before the target reaches the wire, so
the SDK transport rarely presents one; this helper still implements the rule,
because it is also the reference for callers who are not using ``httpx``.)

**Rejected targets** (each is unsignable, and the failure is raised at sign time
rather than surfacing as an unexplainable 401):

* not beginning with ``/`` — absolute-form (``http://host/a``) and ``OPTIONS *``.
  The production parser discards the absolute-form authority before the
  application sees it, so those bytes cannot be signed;
* containing ``#`` — the fragment is discarded by the parser and never reaches the
  origin;
* ending in a bare ``?`` — an empty query is indistinguishable from no query, so
  the target is ambiguous. Send ``/x``, not ``/x?``.

**canonical_query.** An empty (or absent) query string yields the **empty string**
for this field; the ``\\n`` separators remain, so the canonical request always has
eleven fields. Otherwise the field is the query string **VERBATIM**: every byte after
the first ``?`` of the request target, **in the order sent**. **Nothing is split,
sorted, decoded, re-encoded, deduplicated, or reassembled.** Empty elements,
repeated keys, bare keys, ``+``, ``%41`` and hex case all survive because nothing
looks at them:

* ``a=1&&b=2`` canonicalizes to ``a=1&&b=2`` — the empty element is retained;
* ``?flag`` canonicalizes to ``flag`` and ``?flag=`` to ``flag=``. They are
  different requests and produce different signatures;
* ``a=b=c`` is one element and is emitted as one element; there is no key/value
  split to get wrong;
* ``+`` stays ``+``, ``%41`` stays ``%41``, ``%2f`` and ``%2F`` stay distinct.

**The order is part of what is signed.** A sorted query is a sorted *multiset*,
so any two permutations of the same query — ``org=attacker&org=victim`` and
``org=victim&org=attacker`` — would produce one base string and one signature,
while the origin resolves a repeated key to its **last** occurrence and therefore
reads the two as opposite requests. Signing the bytes verbatim fixes the meaning
of the thing signed.

**What this costs, stated because it changes what a caller must do.** The
signature no longer tolerates a reordering of the query between sign time and send
time. Build the request first and sign the target your transport has finished
preparing — which is what this SDK's transport does — rather than signing a target
you assemble separately. A caller that gets this wrong fails **loudly**, with a
401, instead of succeeding with a request whose meaning was never signed.

**api_key in the query.** A signed request carrying an ``api_key`` query parameter
is rejected by the server: a credential in a signed URL defeats
the purpose. :func:`sign_request` and :class:`RequestSigner` refuse it. The pure
:func:`canonical_query` does **not**, so it stays a total function of its input and
can reproduce any published vector.

**Body.** Lowercase hex SHA-256 over the **exact bytes** sent. An absent or empty
body is the SHA-256 of **zero bytes** (:data:`EMPTY_BODY_SHA256`) — never the
digest of ``{}`` and never an empty field. A ``str`` body is UTF-8 encoded with
**no Unicode normalization**: NFC and NFD spellings of the same text are different
bytes and sign differently, so the caller must hash the bytes it will actually
send. Streaming and other non-seekable bodies cannot be signed and raise
:class:`SigningError`.

**Timestamp.** Unix **seconds**, base-10 ASCII: digits only, no sign, no fractional
part, and no leading zero unless the value is exactly ``0``. It appears in the
canonical string byte-identically to ``X-Perceivo-Timestamp``. The server rejects
``|now - timestamp| > 300`` (:data:`MAX_CLOCK_SKEW_SECONDS`).

**Nonce.** 8–128 characters from ``[A-Za-z0-9_-]``, signed exactly as sent, and
generated from a CSPRNG (:func:`generate_nonce`). A counter or a timestamp-derived
value is a client defect the server cannot detect. Nonces are single-use: the
server rejects a replay.

Golden vectors
==============
The rulings above are pinned by a table of published vectors that **ships inside
this package** and is read with :func:`load_golden_vectors` — never by a filesystem
path::

    from perceivo._internal.signing import load_golden_vectors

    for vector in load_golden_vectors()["vectors"]:
        ...  # your canonicaliser must reproduce canonical_request and signature

That one table is what a server-side canonicaliser and this client cross-check
against, which is the whole point: two independent copies cannot drift-check each
other. See :mod:`~perceivo._internal.signing.golden_vectors` for why it is packaged
data rather than a sibling directory, and for why the test secret inside it is
published deliberately.

⭐ **No count is stated here, deliberately**: a number written in prose has
nothing that fails when the table grows. The table declares its own sizes under
``vector_counts``, and ``len()`` of any list is always the truth::

    table = load_golden_vectors()
    table["vector_counts"]  # a key per list; len() of that list always agrees

The lists below mean different things, and ``vector_counts`` gives their sizes:

* ``vectors`` — positive: reproduce ``canonical_request`` and ``signature``.
* ``negative_vectors`` — canonical strings whose **HMAC is correct** and which must still
  be refused, because the binding is wrong: a key id not the one whose secret was fetched,
  an audience not the verifier's, a field 1 not ``perceivo-hmac-v1``, the six-, nine- and
  ten-field downgrades, and a field 9 bound to nothing. A bare MAC check passes them all,
  which is the point; :func:`verify_signed_request` refuses all **but** the field-9 row,
  having no request to re-derive it from; see ``refused_by``.
* ``unsignable_vectors`` — inputs a conforming signer must **refuse**, with no
  ``canonical_request`` or ``signature`` to publish. Field 8: bytes outside ASCII
  printable ``0x20``–``0x7E``. Field 9: organization spellings refused rather than
  folded, one a duplicate header. Canonicalise and require an error.
* ``secret_transport_vectors`` — how a secret *spelling* becomes the HMAC key: a 64-hex
  disclosure is **decoded** to its 32 bytes, never UTF-8-encoded.

Secret handling
===============
The signing secret is never logged, never interpolated into an exception message,
and never rendered by ``repr()`` — :class:`SigningCredentials` and
:class:`RequestSigner` both redact it. :func:`verify_signature` compares with
``hmac.compare_digest``, never ``==``.
"""

from __future__ import annotations

from perceivo._internal.signing._constants import (
    AUDIENCE_LOCAL,
    AUDIENCE_PRODUCTION,
    CANONICAL_AUDIENCE_PATTERN,
    CANONICAL_DOMAIN,
    CANONICAL_FIELD_COUNT,
    CANONICAL_ORGANIZATION_PATTERN,
    EMPTY_BODY_SHA256,
    HEADER_KEY_ID,
    HEADER_NONCE,
    HEADER_ORGANIZATION,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    MAX_CLOCK_SKEW_SECONDS,
    SIGNATURE_PREFIX,
    SIGNATURE_VERSION,
    BodyType,
    ContentTypeType,
    OrganizationType,
    SecretType,
)
from perceivo._internal.signing.canonical import (
    SigningError,
    body_sha256_hex,
    build_canonical_request,
    canonical_audience,
    canonical_content_type,
    canonical_key_id,
    canonical_organization,
    canonical_path,
    canonical_query,
    generate_nonce,
    split_target,
)
from perceivo._internal.signing.golden_vectors import (
    golden_vectors_bytes,
    load_golden_vectors,
)
from perceivo._internal.signing.signer import (
    RequestSigner,
    SignedRequest,
    SigningCredentials,
    compute_signature,
    resolve_signing_credentials,
    sign_request,
    verify_signature,
    verify_signed_request,
)

__all__ = [
    "AUDIENCE_LOCAL",
    "AUDIENCE_PRODUCTION",
    "CANONICAL_AUDIENCE_PATTERN",
    "CANONICAL_DOMAIN",
    "CANONICAL_FIELD_COUNT",
    "CANONICAL_ORGANIZATION_PATTERN",
    "EMPTY_BODY_SHA256",
    "HEADER_KEY_ID",
    "HEADER_NONCE",
    "HEADER_ORGANIZATION",
    "HEADER_SIGNATURE",
    "HEADER_TIMESTAMP",
    "MAX_CLOCK_SKEW_SECONDS",
    "SIGNATURE_PREFIX",
    "SIGNATURE_VERSION",
    "BodyType",
    "ContentTypeType",
    "OrganizationType",
    "RequestSigner",
    "SecretType",
    "SignedRequest",
    "SigningCredentials",
    "SigningError",
    "body_sha256_hex",
    "build_canonical_request",
    "canonical_audience",
    "canonical_content_type",
    "canonical_key_id",
    "canonical_organization",
    "canonical_path",
    "canonical_query",
    "compute_signature",
    "generate_nonce",
    "golden_vectors_bytes",
    "load_golden_vectors",
    "resolve_signing_credentials",
    "sign_request",
    "split_target",
    "verify_signature",
    "verify_signed_request",
]
