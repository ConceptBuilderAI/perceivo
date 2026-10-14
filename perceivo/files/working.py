"""The run's working container: read, write and list, all by path.

    perceivo.files.read(entry_id)              -> bytes   # org store
    perceivo.files.working.read(file_path)     -> str     # this run's scratch

The handle is a module, so it is not callable: ``perceivo.files.working("x")``
fails immediately.
"""

from typing import cast

from .._internal.context import _ExecutionContext
from ..types.pagination import Page
from ..types.records import _from_wire
from ._types import BlobEntry

__all__ = ["list", "read", "write"]

#: The container this namespace addresses. Fixed, not a parameter.
_WORKING = "working"


async def read(file_path: str) -> str:
    """
    Read a file from this run's working storage.

    Args:
        file_path: Path to the file, relative to the execution's working
            directory. Can be a simple filename or a path like
            "subfolder/file.txt".

    Returns:
        The file contents as a string.

    Example:
        ```python
        # Read a simple file
        content = await perceivo.files.working.read("input.txt")

        # Read from a subdirectory
        config = await perceivo.files.working.read("config/settings.json")
        ```
    """
    ext = _ExecutionContext.get_ext()
    return cast(str, await ext.files.read(file_path))


async def write(file_path: str, content: str | bytes) -> str:
    """
    Write a file to this run's working storage.

    Args:
        file_path: Path for the file, relative to the execution's working
            directory. Can be a simple filename or include subdirectories.
        content: The content to write. Can be a string or bytes.

    Returns:
        The URL or path of the saved file.

    Example:
        ```python
        # Write a text file
        url = await perceivo.files.working.write("output.txt", "Hello, world!")

        # Write JSON data
        import json
        url = await perceivo.files.working.write(
            "results.json",
            json.dumps({"status": "success"})
        )
        ```
    """
    ext = _ExecutionContext.get_ext()
    return cast(str, await ext.files.write(content, file_path))


async def list(prefix: str = "", limit: int = 1000) -> Page[BlobEntry]:
    """List files in this run's working storage, filtered by path prefix.

    Takes no container: this namespace is the container. To enumerate a
    platform-written container instead, use :func:`perceivo.files.platform.list`.

    Args:
        prefix: Path prefix to filter by, relative to the execution's working
            directory (e.g., ``"outputs/"``). Defaults to everything.
        limit: Maximum number of entries to return (default 1000).

    Returns:
        A :class:`~perceivo.types.pagination.Page` of
        :class:`~perceivo.files._types.BlobEntry`: ``name`` (the path relative to
        this run's working directory, e.g. ``"outputs/result.txt"``, the same form
        :func:`read` and :func:`write` take), ``size``, ``last_modified``.

        ``total`` stays ``0`` because the result is capped by ``limit``: a full
        page means there may be more.

    Example:
        ```python
        for entry in await perceivo.files.working.list("outputs/"):
            print(entry.name, entry.size)
        ```
    """
    ext = _ExecutionContext.get_ext()
    raw = await ext.files.list_blobs(_WORKING, prefix, limit)
    return Page(data=[_from_wire(BlobEntry, b) for b in raw], page=1, page_size=limit)
