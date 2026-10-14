"""File names an import writes, derived from a bundle or given by the caller.

Imports write ``<stem>.py``, ``<stem>.yaml`` and ``<stem>.component.json`` into
one directory, so a stem must be a single path segment that every host can hold
as an ordinary file. The rules are applied on every OS, because a bundle or a
project imported on one host is opened on others.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from perceivo._internal.paths import PathTraversalError
from perceivo.types.errors import ConfigurationError

# Windows treats these as devices even with an extension ("NUL.txt" is NUL).
_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{port}{digit}" for port in ("COM", "LPT") for digit in "0123456789¹²³"}
)
# Directional formatting characters can make a file name read as something else.
_BIDI_CONTROLS = frozenset(
    "؜‎‏"
    + "".join(chr(c) for c in range(0x202A, 0x202F))
    + "".join(chr(c) for c in range(0x2066, 0x206A))
)


class UnsafeFileNameError(ConfigurationError, ValueError):
    """A component name cannot be held as one ordinary file stem.

    Subclasses ``ConfigurationError`` and ``ValueError``.
    """


def is_reserved_device_name(stem: str) -> bool:
    """Whether Windows reads *stem* (with any extension after it) as a device."""
    return stem.split(".", 1)[0].rstrip(". ").upper() in _RESERVED_DEVICE_NAMES


def safe_stem(name: str) -> str:
    """*name* as one ordinary file stem, or ``UnsafeFileNameError``.

    Refuses separators and traversal, ``:`` (an NTFS stream or drive),
    control and directional formatting characters, and reserved device names.
    """
    stem = Path(name).name
    if (
        not stem
        or stem in {".", ".."}
        or stem != name
        or ":" in name
        or any(unicodedata.category(c) == "Cc" or c in _BIDI_CONTROLS for c in name)
    ):
        raise UnsafeFileNameError(f"Unsafe component name: {name!r}")
    if is_reserved_device_name(name):
        raise UnsafeFileNameError(f"Unsafe component name: {name!r} is a reserved device name")
    return stem


def stem_from_display_name(name: str) -> str:
    """A file stem for a platform display name, which may hold spaces, ``/`` and parentheses.

    A slug that is a device name gains a trailing ``_``, so such a bundle still imports.
    """
    stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "imported"
    return f"{stem}_" if is_reserved_device_name(stem) else stem


def resolve_within(base: Path, filename: str) -> Path:
    """*filename* resolved inside *base*, or ``PathTraversalError`` if a link or ``..`` leads out."""
    base = base.resolve()
    target = (base / filename).resolve()
    if target.parent != base:
        raise PathTraversalError(f"Refusing to write outside {base}: {filename!r}")
    return target
