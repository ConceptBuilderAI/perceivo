"""File storage.

Three namespaces, split on how a file is addressed:

* ``perceivo.files``: the organization's shared file store, by entry id,
  read and write.
* :mod:`perceivo.files.working`: this run's scratch, by path, read, write and
  list.
* :mod:`perceivo.files.platform`: platform-written containers, by path, read
  only. The container is validated against
  :class:`~perceivo.files.StorageContainer`.

Signatures::

    await perceivo.files.list(parent_id=..., entry_type="file")
    await perceivo.files.find(name)                 -> entry | None
    await perceivo.files.read(entry_id)             -> bytes
    await perceivo.files.read_text(entry_id)        -> str
    await perceivo.files.write(file_name, content)  -> {entry, version}
    await perceivo.files.write_text(file_name, text)
    await perceivo.files.delete(entry_id)
    await perceivo.files.mkdir(name) / move

    await perceivo.files.working.read(path)         -> str   # this run's scratch
    await perceivo.files.working.write(path, data)  -> str
    await perceivo.files.working.list(prefix)
    await perceivo.files.platform.read(path, container)      # read only
    await perceivo.files.platform.list(container, prefix)

``perceivo.files.list`` returns ``Page[FileEntry]`` and takes the keyword
names ``parent_id``, ``space_id`` and ``entry_type``. The path-addressed
listings, ``platform.list(container, prefix, limit)`` and
``working.list(prefix, limit)``, return ``Page[BlobEntry]``.
"""

from . import platform, working
from ._org import (
    delete,
    find,
    list,
    mkdir,
    move,
    read,
    read_text,
    write,
    write_text,
)
from ._types import StorageContainer

#: ``rename`` is not exported; rename with ``move``.
__all__ = [
    "StorageContainer",
    "delete",
    "find",
    "list",
    "mkdir",
    "move",
    "platform",
    "read",
    "read_text",
    "working",
    "write",
    "write_text",
]
