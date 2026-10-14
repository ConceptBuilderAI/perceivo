"""Entry type for path-addressed blob containers.

``BlobEntry`` is deliberately not ``FileEntry``: a blob has no entry id and
carries only ``name``, ``size`` and ``last_modified``. It is not exported on
any namespace.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["BlobEntry", "StorageContainer"]


@dataclass(frozen=True)
class BlobEntry:
    """One blob in a path-addressed container.

    There is no id. A blob is addressed by :attr:`name`, which is the full path
    within its container and is what ``platform.read`` and ``working.read``
    take.
    """

    #: The blob's path within its container, relative to the container root.
    #: Always present.
    name: str
    #: Bytes. ``None`` only if the store reported no size.
    size: int | None = None
    #: ISO-8601, or ``None`` when the container reported no timestamp.
    last_modified: str | None = None


class StorageContainer(StrEnum):
    """The platform-written blob containers :mod:`perceivo.files.platform` reads.

    These containers are read only because :mod:`perceivo.files.platform` has no
    write verb. The run's own working container is not a member; it is addressed
    by :mod:`perceivo.files.working`.
    """

    ORCHESTRATIONS = "orchestrations"
    INTELLIGENCE = "intelligence"
    INFRASTRUCTURE = "infrastructure"
