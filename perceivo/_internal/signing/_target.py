"""The request target: splitting it, and canonicalizing its path and query.

This module holds the functions that operate on the *request line* — the
origin-form target an HTTP client puts on the wire — and the percent-escape and
dot-segment machinery they alone use. ``canonical`` keeps the per-field builders
and the assembly of the eleven-field string, and re-exports every name here so
``from perceivo._internal.signing.canonical import canonical_path`` (and the
private ``_decode_unreserved`` / ``_remove_dot_segments`` that the vector tests
reach for) resolve from there.

Pure functions over bytes; no secret passes through this module.
"""

from __future__ import annotations

import re

from perceivo._internal.signing._errors import SigningError

__all__ = [
    "canonical_path",
    "canonical_query",
    "split_target",
]

# Printable ASCII, no space and no control characters: what may appear in an
# origin-form request target.
_TARGET_RE = re.compile(r"\A[\x21-\x7e]*\Z")
# RFC 3986 section 2.3 `unreserved`. These are the ONLY escapes the BASE
# transform decodes; every other escape survives byte-for-byte, hex case
# included.
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
# A well-formed escape. A stray '%' or '%zz' does not match and is therefore
# left alone, which is the correct reading of "left exactly as written".
_PCT_ESCAPE_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def _as_ascii(value: bytes | str, what: str) -> str:
    """Return ``value`` as a printable-ASCII ``str`` or raise :class:`SigningError`."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            value = bytes(value).decode("ascii")
        except UnicodeDecodeError:
            raise SigningError(
                f"{what} must be ASCII; percent-encode non-ASCII bytes before signing"
            ) from None
    if not isinstance(value, str):
        raise SigningError(f"{what} must be str or bytes, got {type(value).__name__}")
    if not _TARGET_RE.match(value):
        raise SigningError(
            f"{what} must be printable ASCII with no spaces or control characters; "
            "percent-encode it exactly as your HTTP client will send it"
        )
    return value


def split_target(target: bytes | str) -> tuple[str, str]:
    """Split an origin-form request target into ``(raw_path, query_string)``.

    ``target`` is the bytes an HTTP client puts on the request line — e.g.
    ``/api/v1/orchestrations?limit=10``. The split is on the **first** ``?``; the
    ``?`` belongs to neither field.

    Raises:
        SigningError: If the target is not origin-form (does not start with
            ``/``), carries a fragment, ends in a bare ``?``, or is not
            printable ASCII.
    """
    text = _as_ascii(target, "request target")
    if not text.startswith("/"):
        raise SigningError(
            "request target must be origin-form and start with '/'; absolute-form "
            "URLs and 'OPTIONS *' cannot be signed because the origin never sees them"
        )
    if "#" in text:
        raise SigningError(
            "request target must not contain a fragment; '#...' is discarded before "
            "the origin sees the request and therefore cannot be signed"
        )
    raw_path, sep, query = text.partition("?")
    if sep and not query:
        raise SigningError(
            "request target must not end in a bare '?'; an empty query is "
            "indistinguishable from no query — send the path without the '?'"
        )
    return raw_path, query


def _decode_unreserved(path: str) -> str:
    """BASE step 1: percent-decode **unreserved** escapes only.

    ``%7e`` and ``%7E`` both become ``~`` — both hex cases decode. Every other
    escape is left exactly as written, hex case included: ``%2F`` stays ``%2F``
    and ``%2f`` stays ``%2f``, because decoding a reserved escape is structural
    and lossy (it would collide ``/a%2Fb`` with ``/a/b``).
    """

    def replace(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else match.group(0)

    return _PCT_ESCAPE_RE.sub(replace, path)


def _remove_dot_segments(path: str) -> str:
    """BASE step 2: RFC 3986 section 5.2.4 ``remove_dot_segments``.

    Implemented as the RFC states it, including the branches an origin-form path
    cannot reach, so it stays checkable against the spec rather than against the
    inputs that happened to be tested.

    ``output`` holds whole segments, each carrying its own leading ``/``, so
    popping one is exactly the RFC's *"remove the last segment and its preceding
    '/'"*.
    """
    remaining = path
    output: list[str] = []
    while remaining:
        remaining = _dot_segment_step(remaining, output)
    return "".join(output)


def _dot_segment_step(remaining: str, output: list[str]) -> str:
    """One iteration of the section 5.2.4 loop; returns the new input buffer.

    Each branch is one lettered RFC step, tried in the RFC's order. ``output`` is
    mutated in place exactly as the RFC's output buffer is.
    """
    if remaining.startswith("../"):  # 2A
        return remaining[3:]
    if remaining.startswith("./"):  # 2A
        return remaining[2:]
    if remaining.startswith("/./"):  # 2B
        return "/" + remaining[3:]
    if remaining == "/.":  # 2B
        return "/"
    if remaining.startswith("/../") or remaining == "/..":  # 2C
        if output:
            output.pop()
        # For "/.." the slice is empty, leaving "/" as the RFC requires.
        return "/" + remaining[4:]
    if remaining in (".", ".."):  # 2D
        return ""
    # 2E — move one segment across
    end = remaining.find("/", 1)
    if end == -1:
        output.append(remaining)
        return ""
    output.append(remaining[:end])
    return remaining[end:]


def canonical_path(raw_path: bytes | str) -> str:
    """Return the canonical form of a request path: the **BASE-normalised** path.

    This is the signature's field 2. It is **not** ``raw_path`` verbatim. Two
    operations are applied, in this order:

    1. **Percent-decode unreserved characters only** — ``A-Z a-z 0-9 - . _ ~``.
       Both hex cases decode; every other escape survives byte-for-byte.
    2. **Remove dot segments** — RFC 3986 section 5.2.4, applied to the output of step 1.

    **Nothing else.** ``//`` is not merged, a trailing ``/`` is not stripped, a
    reserved escape is not decoded, and an escape step 1 did not decode is not
    case-normalised.

    **Why the path is normalised when the query is not:** the platform's Istio
    ingress Envoy applies exactly this transform (``pathNormalization: BASE``) to
    every request path before the origin sees it, and applies **nothing** to the
    query. The transform producing the signature base must equal the transform the
    routing layer applies — so the path gets this one and the query gets none.
    Signing the path as sent instead 401s any request carrying ``%7e``, ``%7E``,
    a ``.`` segment or a ``..`` segment.

    **Step 1 runs before step 2, and the order is load-bearing.** ``%2E`` is
    unreserved, so step 1 decodes it to ``.`` and step 2 must then treat it as a
    dot segment.

    **The transform is not injective, deliberately.** ``/a/%7eb`` and ``/a/~b``
    are two request targets that produce one signature base — a signature commits
    to an *equivalence class* of targets, not to a target. That is safe only
    because every member of the class arrives at the origin as the same
    normalised path, so the class the signature commits to and the class the
    router cannot distinguish are the same class.

    Raises:
        SigningError: If the path is not origin-form, contains ``?`` or ``#``, or
            is not printable ASCII.
    """
    path = _as_ascii(raw_path, "raw_path")
    if not path.startswith("/"):
        raise SigningError(
            "raw_path must be origin-form and start with '/'; absolute-form URLs "
            "and 'OPTIONS *' cannot be signed because the origin never sees them"
        )
    if "?" in path or "#" in path:
        raise SigningError(
            "raw_path must not contain '?' or '#'; pass the query separately "
            "(use split_target() to split a combined target)"
        )
    return _remove_dot_segments(_decode_unreserved(path))


def canonical_query(query_string: bytes | str | None) -> str:
    """Return the canonical form of a raw query string: the bytes, **verbatim**.

    Every byte after the first ``?`` of the request target, in the order sent.
    **Nothing is split, sorted, decoded, re-encoded, deduplicated, or
    reassembled**. An empty or absent query yields ``""``.

    The only work this function does is the character-set check: the query must
    be printable ASCII, exactly as an HTTP client puts it on the request line.

    Raises:
        SigningError: If the query string is not printable ASCII.
    """
    if query_string is None:
        return ""
    return _as_ascii(query_string, "query string")
