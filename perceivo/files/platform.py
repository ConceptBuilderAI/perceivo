"""The platform-written containers, by path, read only.

Nothing in this namespace writes. The container is validated against
:class:`~perceivo.files.StorageContainer` before the call is made, so a
mistyped container raises instead of reading an empty one. The run's own
working container is not a member; use :mod:`perceivo.files.working`.

``list`` returns ``Page[BlobEntry]``: a blob has no entry id.
"""

from typing import cast

from .._internal.context import _ExecutionContext
from ..types.errors import ConfigurationError
from ..types.pagination import Page
from ..types.records import _from_wire
from ._types import BlobEntry, StorageContainer

__all__ = ["list", "read"]


def _container(container: StorageContainer | str) -> str:
    """Resolve to a member value, or fail with the set that was allowed.

    ``ConfigurationError`` rather than ``ValueError``: every error this SDK
    raises is caught by ``except PerceivoError``, and a bare ``ValueError``
    escapes that, which turns a mistyped container into an unhandled exception
    in a component whose error handling is otherwise correct.
    """
    try:
        return StorageContainer(container).value
    except ValueError:
        allowed = ", ".join(repr(c.value) for c in StorageContainer)
        raise ConfigurationError(
            f"{container!r} is not a platform container. Allowed: {allowed}. "
            "The run's own scratch is perceivo.files.working, and the "
            "organization's store is perceivo.files, addressed by entry id."
        ) from None


async def read(path: str, container: StorageContainer | str) -> str:
    """
    Read a file from a platform-written container by its full blob path.

    Unlike :func:`perceivo.files.read`, which addresses the organization's
    store by entry id, this addresses a container by path.

    Args:
        container: A :class:`~perceivo.files.StorageContainer` member, or its
            string value.
        path: Full blob path within the container.

    Returns:
        The file contents as a string.

    Raises:
        ConfigurationError: ``container`` is not a platform container.

    Example:
        ```python
        content = await perceivo.files.platform.read(
            "Intelligence/Tax Research/irc41/source_documents/cases/doc.json",
            StorageContainer.INTELLIGENCE,
        )
        doc = json.loads(content)
        ```
    """
    ext = _ExecutionContext.get_ext()
    return cast(str, await ext.files.read_blob(_container(container), path))


async def list(
    container: StorageContainer | str, prefix: str = "", limit: int = 1000
) -> Page[BlobEntry]:
    """List blobs in a platform container filtered by path prefix.

    Args:
        container: A :class:`~perceivo.files.StorageContainer` member, or its
            string value.
        prefix: Path prefix to filter by. Defaults to the whole container.
        limit: Maximum number of blobs to return (default 1000).

    Returns:
        A :class:`~perceivo.types.pagination.Page` of
        :class:`~perceivo.files._types.BlobEntry`: ``name`` (the path within
        the container), ``size``, ``last_modified``. ``total`` stays ``0``
        because the result is capped by ``limit``.

    Raises:
        ConfigurationError: ``container`` is not a platform container.

    Example:
        ```python
        blobs = await perceivo.files.platform.list(
            StorageContainer.INTELLIGENCE,
            "Intelligence/Tax Research/irc41/source_documents/",
        )
        for blob in blobs:
            print(blob.name, blob.size)
        ```
    """
    ext = _ExecutionContext.get_ext()
    raw = await ext.files.list_blobs(_container(container), prefix, limit)
    return Page(data=[_from_wire(BlobEntry, b) for b in raw], page=1, page_size=limit)
