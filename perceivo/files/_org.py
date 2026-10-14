"""The organization's shared file store, addressed by entry id."""

from typing import Any, Optional, cast

from .._internal.context import _ExecutionContext
from .._internal.mime import guess_text_mime
from ..types.pagination import Page
from ..types.records import FileEntry, _from_wire

_ORG_FILES_UNAVAILABLE = "Organization files service not available"


async def list(
    parent_id: Optional[str] = None,
    space_id: Optional[str] = None,
    entry_type: Optional[str] = None,
) -> Page[FileEntry]:
    """List files and folders in organization storage.

    Args:
        parent_id: ID of the parent folder. If None, lists root level.
        space_id: ID of the space to list. If None, uses default space.
        entry_type: Filter by entry type ("file" or "folder").

    Returns:
        A :class:`~perceivo.types.pagination.Page` of
        :class:`~perceivo.types.records.FileEntry`. Attribute access, not
        subscription.

        Two field names are not the ones you would guess: the kind of entry is
        ``entry_type`` (not ``type``) and a file's size is ``size_bytes`` (not
        ``size``).

        The route takes no page parameter and returns the whole listing, so
        ``total`` is the true count and ``has_next`` is ``False``.

    Example:
        ```python
        # List all files in root
        page = await perceivo.files.list()

        # List files in a specific folder
        page = await perceivo.files.list(parent_id="folder-123")

        # List only files (not folders)
        for f in await perceivo.files.list(entry_type="file"):
            print(f.name, f.entry_type, f.size_bytes)
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    raw = await ext.org_files.list(parent_id=parent_id, space_id=space_id, entry_type=entry_type)
    return Page.of([_from_wire(FileEntry, e) for e in raw])


async def read(entry_id: str) -> bytes:
    """Read a file from organization storage.

    Takes an entry id and returns bytes. To read this run's scratch by path, use
    :func:`perceivo.files.working.read`.

    Args:
        entry_id: The ID of the file to read.

    Returns:
        The file contents as bytes.

    Example:
        ```python
        content = await perceivo.files.read("file-123")
        text = content.decode("utf-8")
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    return cast(bytes, await ext.org_files.read(entry_id))


async def read_text(entry_id: str, encoding: str = "utf-8") -> str:
    """
    Read a file from organization storage as text.

    Args:
        entry_id: The ID of the file to read.
        encoding: Text encoding (default: "utf-8").

    Returns:
        The file contents as a string.

    Example:
        ```python
        text = await perceivo.files.read_text("file-123")
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    return cast(str, await ext.org_files.read_text(entry_id, encoding=encoding))


async def write(
    file_name: str,
    content: str | bytes,
    parent_id: Optional[str] = None,
    space_id: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> dict[str, Any]:
    """Write a file to organization storage.

    Takes a file name plus placement and returns an ``{entry, version}`` dict. To
    write this run's scratch by path, use :func:`perceivo.files.working.write`.

    Args:
        file_name: Name for the file.
        content: File content (string or bytes).
        parent_id: ID of the parent folder. If None, writes to root.
        space_id: ID of the space. If None, uses default space.
        mime_type: MIME type of the file. If omitted, the type comes from the
            file name's suffix, else ``text/plain``, locally and on the platform.

    Returns:
        Dict with two top-level keys, ``entry`` and ``version``, not a flat
        entry dict. The file's ID lives at ``result["entry"]["id"]``.

            - entry: The created file entry, containing:
                - id: File ID
                - name: File name
                - path: Full path within the space
                - entry_type: "file"
                - parent_id: ID of the containing folder (None at root)
                - mime_type: MIME type
                - size_bytes: File size in bytes
                - blob_name: Backing blob name
                - created_at / updated_at: ISO 8601 timestamps
            - version: The initial file version, containing:
                - id: Version ID
                - version_number: Starts at 1
                - size_bytes: Size of this version in bytes
                - checksum_md5: Content MD5 (if provided)
                - is_current: True for the latest version
                - created_at: ISO 8601 timestamp

    Example:
        ```python
        result = await perceivo.files.write(
            "report.txt",
            "This is the report content"
        )
        # The entry is nested under "entry"; result["id"] does not exist.
        print(f"Created file: {result['entry']['id']}")
        print(f"Size: {result['entry']['size_bytes']} bytes")
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    # A hosted upload stores the declared part type, and an undeclared one becomes
    # application/octet-stream, so the type is resolved before it is sent.
    return cast(
        dict[str, Any],
        await ext.org_files.write(
            file_name=file_name,
            content=content,
            parent_id=parent_id,
            space_id=space_id,
            mime_type=mime_type or guess_text_mime(file_name),
        ),
    )


async def write_text(
    file_name: str,
    content: str,
    parent_id: Optional[str] = None,
    space_id: Optional[str] = None,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Write a text file to organization storage.

    The media type is resolved from ``file_name`` by ``mimetypes.guess_type``,
    falling back to ``text/plain``. There is no ``content_type`` parameter; to
    force a media type, call :func:`write` with ``mime_type=`` and encoded
    content.

    Args:
        file_name: Name for the file.
        content: Text content to write.
        parent_id: ID of the parent folder. If None, writes to root.
        space_id: ID of the space. If None, uses default space.
        encoding: Text encoding (default: "utf-8").

    Returns:
        Same shape as :func:`write`: a dict with ``entry`` and ``version`` keys,
        not a flat entry dict. The file's ID is at ``result["entry"]["id"]``.

    Example:
        ```python
        result = await perceivo.files.write_text(
            "notes.txt",
            "Meeting notes..."
        )
        print(f"Created file: {result['entry']['id']}")
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    return cast(
        dict[str, Any],
        await ext.org_files.write_text(
            file_name=file_name,
            content=content,
            parent_id=parent_id,
            space_id=space_id,
            encoding=encoding,
        ),
    )


async def mkdir(
    name: str, parent_id: Optional[str] = None, space_id: Optional[str] = None
) -> dict[str, Any]:
    """
    Create a folder in organization storage.

    Args:
        name: Name for the new folder.
        parent_id: ID of the parent folder. If None, creates in root.
        space_id: ID of the space. If None, uses default space.

    Returns:
        A single folder entry dict (NOT an ``{entry, version}`` wrapper like
        :func:`write`). Contains, among other fields:
            - id: Folder ID
            - name: Folder name
            - entry_type: "folder"
            - path: Full path within the space
            - parent_id: ID of the containing folder (None at root)

    Example:
        ```python
        folder = await perceivo.files.mkdir("Reports")
        print(f"Created folder: {folder['id']}")
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    return cast(
        dict[str, Any],
        await ext.org_files.create_folder(folder_name=name, parent_id=parent_id, space_id=space_id),
    )


async def rename(entry_id: str, new_name: str) -> dict[str, Any]:
    """Rename a file or folder.

    Args:
        entry_id: ID of the file or folder to rename.
        new_name: New name for the entry.

    Returns:
        A single entry dict for the renamed file or folder (id, name, entry_type,
        size_bytes, path, ...), not an ``{entry, version}`` wrapper.

    Example:
        ```python
        await perceivo.files.rename("file-123", "new_name.txt")
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    return cast(dict[str, Any], await ext.org_files.rename(entry_id, new_name))


async def delete(entry_id: str, permanent: bool = False) -> None:
    """
    Delete a file or folder.

    Args:
        entry_id: ID of the file or folder to delete.
        permanent: If True, permanently delete. If False, move to trash. A
            local run moves the item to ``files/trash/`` under the local-runner
            root, in a new folder per delete that keeps the item's path.

    Example:
        ```python
        # Move to trash
        await perceivo.files.delete("file-123")

        # Permanently delete
        await perceivo.files.delete("file-123", permanent=True)
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    await ext.org_files.delete(entry_id, permanent=permanent)


async def move(entry_id: str, new_parent_id: str) -> dict[str, Any]:
    """Move a file or folder to a new location.

    Args:
        entry_id: ID of the file or folder to move.
        new_parent_id: ID of the destination folder.

    Returns:
        A single entry dict for the moved file or folder (id, name, entry_type,
        size_bytes, path, parent_id, ...), not an ``{entry, version}`` wrapper.

    Example:
        ```python
        await perceivo.files.move("file-123", "folder-456")
        ```
    """
    ext = _ExecutionContext.get_ext()
    if ext.org_files is None:
        raise RuntimeError(_ORG_FILES_UNAVAILABLE)
    return cast(dict[str, Any], await ext.org_files.move(entry_id, new_parent_id))


async def find(
    name: str,
    space_id: Optional[str] = None,
) -> Optional[FileEntry]:
    """Find a storage entry by name.

    Returns one entry or None, not a page, so ``if entry is not None`` works as
    a guard.

    Args:
        name: Name of the file or folder to find.
        space_id: ID of the space to search. If None, uses the default space.

    Returns:
        One :class:`~perceivo.types.records.FileEntry`, or ``None`` if no entry
        matches. Attribute access; the two names to know are ``entry_type``
        and ``size_bytes``, not ``type``/``size``.

    Example:
        ```python
        entry = await perceivo.files.find("report.pdf")
        if entry is not None:
            content = await perceivo.files.read(entry.id)
        ```
    """
    try:
        ext = _ExecutionContext.get_ext()
    except RuntimeError:
        return None
    if ext.org_files is None:
        return None
    impl = getattr(ext.org_files, "find", None)
    if callable(impl):
        found = await impl(name=name, space_id=space_id)
        return None if found is None else _from_wire(FileEntry, found)
    # Fallback: scan top-level listings as raw dicts, without building a
    # ``FileEntry`` for every entry.
    entries = await ext.org_files.list(space_id=space_id)
    for entry in entries:
        if entry.get("name") == name:
            return _from_wire(FileEntry, entry)
    return None
