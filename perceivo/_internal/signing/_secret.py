"""How a secret *spelling* becomes HMAC *key bytes*.

One function, and it is the whole of the mapping between what a caller holds and
what the verifier holds. Getting it wrong in one direction produces a green test
suite and a 401 on every production request, so it gets its own module.

The two halves of the contract
==============================
* **The HMAC key is 32 raw bytes.** The platform generates them with
  ``secrets.token_bytes(32)``, seals those bytes, and its verifier feeds the
  unsealed value straight into ``hmac.new(secret, ..., sha256)`` **with no decoding
  step**. The sealed plaintext *is* the key.
* **What the customer is handed is a transportable spelling of those bytes** —
  ``secret.hex()``, 64 lowercase hex characters, disclosed exactly once at
  enrolment. Hex is chosen to avoid client bugs: the signature on the wire is
  already ``v1=<64 hex>``, and hex has no padding and no ``+``/``/`` versus
  ``-``/``_`` alphabet split — which is the most common way a base64-transported
  secret is decoded into the wrong bytes and reported as "signing is broken".

So the caller holds **text** and the verifier holds **bytes**, and the only mapping
between them is ``bytes.fromhex``.

⛔ The failure this prevents
===========================
UTF-8-**encoding** the disclosure produces a **64-byte** key against the server's
**32-byte** one, and every signature mismatches. The failure has no distinguishing
symptom — both sides compute a well-formed HMAC-SHA256, the digests differ, and
the verifier returns the same 401 that a wrong key id or a stale timestamp
returns. There is nothing to read in a log on either side.

⭐ The golden-vector table's positive rows are keyed on ``secret_utf8``, 31 bytes of
ASCII, which is not the disclosure shape, so a wrong implementation reproduces
every one of them. ``table["secret_transport_vectors"]`` covers the encoding,
including the signature the wrong encoding produces, so an implementer who
matches that row knows *which* bug they have.

Why the shape is detected, not declared
=======================================
The alternatives were an explicit ``signing_secret_hex=`` parameter or a
``from_provisioned()`` constructor. Both leave the footgun fully armed: a caller who
passes the disclosure to the ordinary parameter — which is what every quickstart,
every ``export PERCEIVO_SIGNING_SECRET=...`` and every copy-paste looks like — still
gets the silent 401, and *that silent 401 is the defect*. A fix whose correctness
depends on the caller already knowing the thing they got wrong is not a fix.
Detection makes the ordinary path correct with no ceremony, and ``bytes`` remains
the unambiguous explicit form for anyone who wants it.

⚠️ The residual, stated plainly
===============================
A secret that is genuinely a 64-character *passphrase* drawn entirely from the hex
alphabet, intended to be keyed as its own UTF-8 bytes, is decoded instead. That
collision cannot be resolved by inspection, and it is resolved in favour of the
disclosure because this platform issues **only** the disclosure shape,
``token_bytes(32).hex()``. A caller who really means "key on this text" says so by passing ``bytes``:
``passphrase.encode()``.
"""

from __future__ import annotations

import re

from perceivo._internal.signing._constants import SecretType
from perceivo._internal.signing.canonical import SigningError

__all__ = ["secret_to_key_bytes"]

#: The exact shape this platform discloses a signing secret in: 32 bytes of
#: ``secrets.token_bytes`` rendered by ``bytes.hex()``, so 64 hex characters.
#: Matched case-insensitively because ``bytes.fromhex`` is case-insensitive and both
#: spellings decode to the identical 32 bytes — there is nothing to disambiguate, so
#: rejecting an uppercased copy would be ceremony that buys nothing and costs a
#: working client. The length is part of the gate: a shorter or longer hex-alphabet
#: string is not a disclosure and is keyed as text.
_DISCLOSED_SECRET_HEX_RE = re.compile(r"[0-9a-fA-F]{64}")

#: Length of the key material behind that disclosure. Asserted after decoding so a
#: future change to the disclosure format cannot quietly shorten the key.
_SIGNING_SECRET_BYTES = 32


def secret_to_key_bytes(secret: SecretType) -> bytes:
    """Return the HMAC key for ``secret``: 32 raw bytes, never a re-encoding.

    ⛔ **A ``str`` in the platform's disclosure shape is HEX AND IS DECODED, not
    encoded.** See the module docstring for why that is the whole correctness of
    this function, and for the residual it accepts.

    ``bytes`` is passed through verbatim and is the explicit escape hatch. Any other
    ``str`` — including the golden-vector table's own ``secret_utf8``, 31 bytes and
    not the hex shape — keys on its own UTF-8 bytes, which keeps every published
    positive vector reproducible.

    Surrounding whitespace is stripped **before the shape is tested and before the
    decode**, because ``$(cat secret.txt)`` and a pasted value carry a trailing
    newline and a newline is not key material. It is stripped only on the hex path:
    a text secret keeps every byte it was given.
    """
    if isinstance(secret, str):
        candidate = secret.strip()
        if _DISCLOSED_SECRET_HEX_RE.fullmatch(candidate):
            raw = bytes.fromhex(candidate)
            if len(raw) != _SIGNING_SECRET_BYTES:  # pragma: no cover - shape guarantees it
                raise SigningError(
                    "signing secret decoded from hex to the wrong length; expected "
                    f"{_SIGNING_SECRET_BYTES} bytes"
                )
        else:
            raw = secret.encode("utf-8")
    elif isinstance(secret, (bytes, bytearray, memoryview)):
        raw = bytes(secret)
    else:
        raise SigningError(f"signing secret must be str or bytes, got {type(secret).__name__}")
    if not raw:
        raise SigningError("signing secret is empty")
    return raw
