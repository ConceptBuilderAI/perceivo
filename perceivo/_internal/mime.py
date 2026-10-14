"""Resolve the media type ``write_text``, and ``write`` without one, send.

The platform's file upload route stores the content type it is sent, falling
back to ``mimetypes.guess_type`` and then ``application/octet-stream``. This
module applies the same rule with one difference: a name with no recognised
suffix resolves to ``text/plain`` rather than ``application/octet-stream``. A suffix that maps to a binary type, such as
``.png``, resolves to that type.
"""

from __future__ import annotations

import mimetypes

# An unrecognised suffix falls back to text rather than to the host's
# ``application/octet-stream``, so a local run and the platform store one type.
TEXT_FALLBACK = "text/plain"


def guess_text_mime(file_name: str) -> str:
    """Return the media type for a text upload named ``file_name``.

    Uses the same ``mimetypes.guess_type`` call the platform uses, so the two
    agree. Never returns ``None``.
    """
    guessed, _encoding = mimetypes.guess_type(file_name)
    return guessed or TEXT_FALLBACK
