"""The credential object, its key-id validation, and how one is resolved.

The seam is lifetime. ``signer`` signs one request at a time; this module holds
the long-lived object a caller configures once -- what a key id must normalise to
before it can be both signed and sent, what a credential may and may not disclose
about the secret it holds, and where a credential is allowed to come from.

⛔ The secret is never logged, never interpolated into an exception message, and
never rendered by ``repr()``.

``SigningCredentials`` and ``resolve_signing_credentials`` are re-exported from
``perceivo._internal.signing.signer`` and from ``perceivo._internal.signing``; both
remain supported import paths and hand back the SAME objects.
"""

from __future__ import annotations

import os
import re
from types import MemberDescriptorType

from perceivo._internal.signing._constants import (
    ENV_KEY_ID,
    ENV_SIGNING_SECRET,
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
    "SigningCredentials",
    "resolve_signing_credentials",
]

# A normalised key id — the lowercase hyphenated UUID `canonical_key_id` emits —
# is by construction printable ASCII with no space and no control character, so
# CR/LF is structurally unrepresentable in the header this module hands out. This
# pattern is the POST-condition, asserted rather than trusted.
_KEY_ID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def _validate_key_id(key_id: str | object) -> str:
    """Return the **normalised** ``key_id`` — the form that is signed and sent.

    Normalisation is delegated to
    :func:`~perceivo._internal.signing.canonical.canonical_key_id`, so the value in
    ``X-Perceivo-Key-Id`` is byte-identical to canonical field 3. Emitting one
    spelling in the header and signing another would break the verifier's binding
    of field 3 for no visible reason.

    ⚠️ **Defence in depth, and deliberately so.** A CRLF in ``key_id`` does not
    reach the wire today: driving a raw socket shows ``h11`` rejects the header and
    nothing is sent. But that is the *HTTP encoder's* guarantee, not this module's,
    and it is exactly the kind of guarantee that evaporates when a caller swaps
    transports or builds the request by hand from ``SignedRequest.headers``. This
    module hands out header values, so this module validates them — here as an
    assertion on the normaliser's output rather than a filter on its input.
    """
    normalised = canonical_key_id(key_id)
    if not _KEY_ID_RE.match(normalised):  # pragma: no cover - unreachable by construction
        raise SigningError(
            "key_id did not normalise to the lowercase hyphenated UUID form; it is "
            "sent as an HTTP header value and signed as canonical field 3"
        )
    return normalised


class SigningCredentials:
    """A key id, its signing secret, and the audience it signs for.

    Immutable, and the secret never appears in ``repr()``.

    ⛔ **Deliberately NOT a dataclass, and that is a secret-containment measure.**
    ``__slots__`` removes ``__dict__``, which the generic "dump the object" idioms
    (error reporters, structured loggers, ``pprint`` of a context dict) reach for.
    But ``@dataclass`` would also stamp ``__dataclass_fields__`` on the class, and
    ``dataclasses.asdict(creds)`` and ``dataclasses.astuple(creds)`` walk *that* —
    reaching the raw secret and rendering it into a plain ``dict`` that any log
    line will happily print.

    Written out by hand, the class has no ``__dataclass_fields__``, so
    :func:`dataclasses.asdict`, :func:`dataclasses.astuple`,
    :func:`dataclasses.fields` and :func:`dataclasses.replace` all raise
    ``TypeError`` instead of silently succeeding. Everything a caller legitimately
    does — construct it, read ``key_id``/``audience``/``signing_secret``, compare
    two instances, put one in a ``set`` — is unchanged, and ``__match_args__`` is
    declared so structural pattern matching still works.

    ⚠️ **Two extraction routes remain open, and they are the caller's to respect.**

    * ``creds.signing_secret`` returns the secret **exactly as it was passed in**,
      which for a provisioned key is the 64-hex disclosure and not the 32-byte HMAC
      key. It **must** be readable: the signer reads it, and normalises it through
      :func:`_secret_bytes` at signing time rather than at construction, so the
      credential holds one copy of one spelling. Containment here means a dump has
      to *name* the attribute, never that the value is unreachable.
    * ``creds.__getstate__()`` — and therefore ``pickle.dumps(creds)`` — still
      walks the slots and carries the secret. It is left working on purpose: a
      ``__getstate__`` that raised would break ``multiprocessing`` for any
      configuration object holding credentials, and one that redacted would
      round-trip to an object that silently fails to authenticate, surfacing as
      a 401 far from its cause. **Do not pickle, serialise or
      ``deepcopy``-then-log a ``SigningCredentials``.**

    ⭐ **``__getstate__`` and ``__setstate__`` are written out explicitly.** Without
    them, unpickling assigns through ``__setattr__`` and a frozen class refuses its
    own restore, so ``copy.deepcopy(creds)`` would raise ``AttributeError``.
    Closing a leak must not cost the caller a working ``deepcopy``.

    ⛔ **``deepcopy`` makes an independent credential, and it has to.** ``frozen``
    binds the *binding*, not the referent, and the accepted secret types include
    ``bytearray`` and ``memoryview``. A ``__deepcopy__`` that returned ``self``
    would therefore hand back a "snapshot" that shares the caller's buffer, so
    rotating that buffer in place would change the key the snapshot signs with —
    a deep copy that is not one. The restore path above is what makes it a real
    copy, which is also why the caller rule above names ``deepcopy``: an
    independent copy is a *second* copy of the secret, and logging it leaks.
    """

    __slots__ = ("audience", "key_id", "signing_secret")

    #: Positional structural pattern matching. ``audience`` is keyword-only and so is correctly absent here.
    __match_args__ = ("key_id", "signing_secret")

    #: ⭐ ``audience`` has no default, deliberately. It is a deployment constant the
    #: verifier binds against, and there is no value this package could guess that
    #: would be right anywhere: a wrong guess produces a signature that fails at
    #: every deployment, and a *plausible* wrong guess produces one that fails at
    #: all but one. Carrying it on the credentials means a ``RequestSigner``
    #: cannot exist without one, so no signing path can reach the wire unaudienced.
    def __init__(self, key_id: str, signing_secret: SecretType, *, audience: str) -> None:
        # Frozen: assign through object.__setattr__. The stored key id is the
        # normalised one, so the key id the signer sends is byte-identical to the
        # one it signs.
        object.__setattr__(self, "key_id", _validate_key_id(key_id))
        object.__setattr__(self, "signing_secret", signing_secret)
        object.__setattr__(self, "audience", canonical_audience(audience))
        # Validates shape and emptiness without retaining or exposing the value.
        _secret_bytes(signing_secret)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"SigningCredentials is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"SigningCredentials is immutable; cannot delete {name!r}")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SigningCredentials):
            return NotImplemented
        return (self.key_id, self.signing_secret, self.audience) == (
            other.key_id,
            other.signing_secret,
            other.audience,
        )

    def __hash__(self) -> int:
        return hash((self.key_id, self.signing_secret, self.audience))

    # ⭐ Pickle and `copy.deepcopy` support. A frozen class cannot be restored through
    # `setattr`, so the restore goes through `object.__setattr__` — the same door
    # `__init__` uses. Validation is deliberately NOT re-run on restore: the state
    # came from an instance that already passed it, and re-running `_validate_key_id`
    # would make unpickling raise `SigningError` from inside `pickle.loads`.
    #
    # ⛔ **The slots are resolved across the MRO, not read off `self.__slots__`.**
    # `self.__slots__` is an ordinary attribute lookup, so on a subclass that
    # declares any slots of its own it resolves to the SUBCLASS's tuple and the
    # base's three names are simply absent from the state. The round trip then
    # succeeds and returns a credential with no `key_id`, no `signing_secret` and
    # no `audience` — `RequestSigner(restored).sign()` raises `AttributeError` far
    # from the pickle that caused it. Subclassing a credential to carry a tenant or
    # a region is an ordinary thing to do, so this walks `__mro__` instead.
    #
    # The walk reads the member descriptors each class actually installed rather
    # than re-parsing its `__slots__` declaration: that spelling is what the
    # interpreter itself ended up with, so it handles a bare-string `__slots__`, a
    # private slot the compiler name-mangled, and it excludes `__dict__` and
    # `__weakref__` (which are getset descriptors, not member descriptors) without
    # special-casing any of them.
    def __getstate__(self) -> dict[str, object]:
        state: dict[str, object] = {}
        for klass in type(self).__mro__:
            for name, member in vars(klass).items():
                if not isinstance(member, MemberDescriptorType) or name in state:
                    continue
                try:
                    state[name] = getattr(self, name)
                except AttributeError:
                    # A declared slot that was never assigned stays unassigned on
                    # restore, rather than being restored as some invented value.
                    continue
        return state

    def __setstate__(self, state: dict[str, object]) -> None:
        for name, value in state.items():
            object.__setattr__(self, name, value)

    # A shallow copy of an immutable value object is the object. Returning `self` is
    # what `copy` does for `str` and `tuple`, and it is not a containment claim: the
    # default shallow copy would share the very same secret object anyway, because
    # that is what "shallow" means.
    #
    # ⛔ **There is deliberately NO `__deepcopy__` override; one that returns `self`
    # would be a defect, not an optimisation.** `frozen` binds the
    # *binding*, never the referent, and `_secret_bytes` admits `bytearray` and
    # `memoryview` — so a `__deepcopy__` returning `self` makes the "snapshot" ALIAS
    # the caller's buffer, and a caller that rotates that buffer in place silently
    # changes the key the copy signs with. Without the override, `deepcopy` goes
    # through `__getstate__`/`__setstate__` and copies the secret, which is the
    # independence `deepcopy` is asked for. Nothing is saved by the override
    # either: `deepcopy` of a `str` or `bytes` returns the identical object anyway.
    def __copy__(self) -> SigningCredentials:
        return self

    def __repr__(self) -> str:
        return (
            f"SigningCredentials(key_id={self.key_id!r}, signing_secret=<redacted>, "
            f"audience={self.audience!r})"
        )

    __str__ = __repr__


def resolve_signing_credentials(
    *,
    key_id: str | None = None,
    signing_secret: SecretType | None = None,
    audience: str | None = None,
) -> SigningCredentials | None:
    """Resolve signing credentials: the two secrets may come from the environment,
    the audience never does.

    ``key_id`` and ``signing_secret`` fall back to ``PERCEIVO_KEY_ID`` and
    ``PERCEIVO_SIGNING_SECRET`` — they are *credentials*, and credentials belong in
    the environment. ``audience`` has **no** environment fallback and no default:
    it is explicit configuration, supplied here or not at all. It must equal the
    deployment's own ``HMAC_AUDIENCE``; if the two are spelled differently every
    signed request fails closed, which is the correct direction but is worth
    checking first when a working client suddenly 401s everywhere.

    ⛔ The audience is never derived — not from ``base_url``, not from a ``Host``
    header, not from an ambient environment variable. Each of those is a value some
    *other* system chose, and a signature is only worth the deliberateness of the
    binding behind it.

    Returns:
        ``None`` when neither credential is configured — request signing is
        **opt-in and off by default**, and an unconfigured client sends exactly
        what it always did. An audience alone is not configuration: it is inert
        without a key, so it does not switch signing on.

    Raises:
        SigningError: When the configuration is partial. Silently not signing
            would leave a caller who believes signing is on unprotected, and
            silently defaulting the audience would produce signatures that verify
            nowhere — or, worse, at one deployment by accident.
    """
    resolved_id = key_id or os.environ.get(ENV_KEY_ID)
    resolved_secret = signing_secret or os.environ.get(ENV_SIGNING_SECRET)
    resolved_audience = audience
    if not resolved_id and not resolved_secret:
        return None
    if not resolved_id:
        raise SigningError(
            f"a signing secret was configured without a key id; set key_id=... or {ENV_KEY_ID}"
        )
    if not resolved_secret:
        raise SigningError(
            "a key id was configured without a signing secret; set signing_secret=... "
            f"or {ENV_SIGNING_SECRET}"
        )
    if not resolved_audience:
        raise SigningError(
            "signing credentials were configured without an audience; pass audience=... "
            "(e.g. 'perceivo-api-prod') matching the deployment's own HMAC_AUDIENCE. "
            "It is explicit configuration: there is no environment fallback, and it is "
            "never a hostname and never derived from base_url."
        )
    return SigningCredentials(
        key_id=resolved_id, signing_secret=resolved_secret, audience=resolved_audience
    )
