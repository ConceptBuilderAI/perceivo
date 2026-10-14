"""The one exception type the signing package raises.

It lives in its own module for a structural reason, not a stylistic one: it
breaks two independent import cycles. ``SigningError`` is raised by
``_target`` (percent-decoding, dot-segment removal, target splitting) and by
``canonical`` (which consumes ``_target``), so defining it in either of them
makes the other import its importer. And ``canonical`` re-exports the field 8
and field 9 canonicalisers from ``_header_fields``, which must raise the same
class — so a definition in ``canonical`` is a cycle from that direction too. A
shared leaf, like ``_constants``, is the version with no direction to get wrong.
Nothing here has behaviour, so no module that only needs to *raise* has to pull
in a canonicaliser to do it.

``SigningError`` is re-exported from ``perceivo._internal.signing.canonical`` and
from ``perceivo._internal.signing``; both remain the supported import paths and are
the SAME class object, so ``except SigningError`` written against either one
still catches this.
"""

from __future__ import annotations

__all__ = ["SigningError"]


class SigningError(ValueError):
    """A request could not be signed, or signing input was malformed.

    Never carries the signing secret in its message.
    """
