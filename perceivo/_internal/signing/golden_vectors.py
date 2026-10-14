"""The published golden-vector table, shipped as importable package data.

The table is the **cross-implementation contract** for ``v1`` request signing: a
``(method, target, body, timestamp, nonce, secret) -> (canonical_request,
signature)`` mapping that any signer or verifier — this client, the platform's
verifier, a customer's Go or Node implementation — must reproduce byte for byte.
This cross-check is the primary defence against signer/verifier drift, because
two independent copies of a table cannot drift-check each other; there has to be
exactly one table and both sides have to read it.

⛔ **The table ships as package data so that it travels with the installed
package.** A data file outside the package directory is not included in a built
wheel, which would leave a consumer with no path to the contract at all.

Read it through :func:`load_golden_vectors`. Do **not** reach for a filesystem
path: the resource is addressed with :mod:`importlib.resources`, so it resolves
identically from a source checkout, an installed wheel, and a zipimport, and the
accessor stays a stable public surface while the file's location stays an internal
detail.

    from perceivo._internal.signing import load_golden_vectors

    table = load_golden_vectors()
    for vector in table["vectors"]:
        ...  # reproduce vector["canonical_request"] and vector["signature"]

The signing secret in the table
===============================
``table["secret_utf8"]`` holds a key that is **published on purpose and protects
nothing** — a vector table without its key is not a table, because the signature
column cannot be recomputed without it. It is not a credential, it authenticates
nothing, and it must never be removed "for safety": doing so would silently reduce
the contract to an unverifiable list of strings. What must never happen is the
reverse — a *real* signing secret being generated into the table and shipped.

⛔ **``secret_utf8`` is not the shape this platform issues, so the positive vectors
alone are blind on one axis.** A real secret is 32 raw bytes disclosed as **64
lowercase hex characters**, so a caller holds text and the verifier holds bytes and
the mapping between them is ``bytes.fromhex``. ``secret_utf8`` is 31 bytes of ASCII
and matches no part of that shape, so a signer that **UTF-8-encodes the disclosure
instead of decoding it** reproduces every positive vector byte for byte and then
fails every production request with an ordinary 401 that names no cause.
``table["secret_transport"]`` states the encoding contract and ``table["secret_transport_vectors"]`` pins it in
**both** directions — including, deliberately, the signature the *wrong* encoding
produces, so an implementer who matches it knows which bug they have instead of
staring at an unfalsifiable 401. A consumer that reads only ``vectors`` is not
merely incomplete on this platform; it is untested against the only key spelling
the platform ever hands out.

Byte stability
==============
The file's bytes *are* the wire format. Changing a vector's target, canonical
request, or signature changes what every implementation must produce, and is a
breaking change to the signing contract — not an edit. Use
:func:`golden_vectors_bytes` to hash the shipped table if you need to pin the
revision you validated against.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

__all__ = ["golden_vectors_bytes", "load_golden_vectors"]

#: Package-relative location of the table. Internal — address the data through the
#: accessors below, never by rebuilding this path against ``__file__``.
_RESOURCE_PACKAGE = "perceivo._internal.signing"
_RESOURCE_DIRECTORY = "vectors"
_RESOURCE_NAME = "hmac_v1_vectors.json"


@lru_cache(maxsize=1)
def _read_bytes() -> bytes:
    """Read the packaged table once.

    Caching *bytes* rather than the parsed table is deliberate: bytes are immutable,
    so the cache cannot hand two callers the same mutable dict and let one of them
    edit the other's contract.
    """
    resource = files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_DIRECTORY, _RESOURCE_NAME)
    return resource.read_bytes()


def golden_vectors_bytes() -> bytes:
    """Return the packaged table's exact bytes.

    For pinning: ``hashlib.sha256(golden_vectors_bytes()).hexdigest()`` identifies
    the revision of the wire-format contract an implementation was validated
    against.
    """
    return _read_bytes()


def load_golden_vectors() -> dict[str, Any]:
    """Return the parsed golden-vector table.

    A fresh object every call — the caller owns it and may mutate it without
    affecting anyone else's view of the contract.
    """
    return json.loads(_read_bytes())
