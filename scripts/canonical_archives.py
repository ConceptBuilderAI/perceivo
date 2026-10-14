"""Canonicalize Python distribution archives without changing member payloads."""

from __future__ import annotations

import gzip
import io
import os
import re
import tarfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Final

CANONICAL_FILE_MODE: Final = 0o644
CANONICAL_EXEC_MODE: Final = 0o755
REGULAR_FILE_TYPE: Final = 0o100000
DIRECTORY_TYPE: Final = 0o040000
ZIP_UNIX_CREATE_SYSTEM: Final = 3
MSDOS_DIRECTORY_FLAG: Final = 0x10
# The MS-DOS timestamp a zip entry carries cannot represent an instant before
# 1980-01-01T00:00:00Z, so a smaller epoch is rejected rather than silently
# rounded up into a value the two builds would not agree on.
ZIP_MINIMUM_EPOCH: Final = 315532800
DRIVE_QUALIFIED: Final = re.compile(r"^[A-Za-z]:")


class VerificationError(RuntimeError):
    """The public boundary is incomplete or contains denied material."""


def _path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or "\\" in path:
        raise VerificationError(f"unsafe path: {path!r}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise VerificationError(f"unsafe path segments: {path!r}")
    return candidate


def _member_name(raw: str) -> str:
    """Return the safe POSIX name of an archive member or reject the member."""
    if DRIVE_QUALIFIED.match(raw):
        raise VerificationError(f"unsafe drive-qualified path: {raw!r}")
    return _path(raw).as_posix()


def _canonical_mode(mode: int, *, executable: bool) -> int:
    return CANONICAL_EXEC_MODE if executable or bool(mode & 0o111) else CANONICAL_FILE_MODE


def resolve_epoch(explicit: int | None) -> int:
    """Resolve the source epoch from ``--epoch`` or ``SOURCE_DATE_EPOCH``."""
    if explicit is not None:
        epoch = explicit
    else:
        raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
        if not raw:
            raise VerificationError("no source epoch: pass --epoch or set SOURCE_DATE_EPOCH")
        try:
            epoch = int(raw)
        except ValueError as exc:
            raise VerificationError(f"invalid SOURCE_DATE_EPOCH: {raw!r}") from exc
    if epoch < 0:
        raise VerificationError(f"negative source epoch: {epoch}")
    return epoch


def _read_sdist_members(source: Path) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    """Read and validate every sdist member before any output is written."""
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    seen: set[str] = set()
    with tarfile.open(source, "r:gz") as archive:
        for original in archive.getmembers():
            name = _member_name(original.name)
            if name in seen:
                raise VerificationError(f"duplicate archive member in {source.name}: {name}")
            seen.add(name)
            if original.issym() or original.islnk():
                raise VerificationError(f"link archive member in {source.name}: {name}")
            if original.ischr() or original.isblk() or original.isfifo():
                raise VerificationError(f"device archive member in {source.name}: {name}")
            payload: bytes | None
            if original.isdir():
                payload = None
            elif original.isfile():
                extracted = archive.extractfile(original)
                if extracted is None:
                    raise VerificationError(f"cannot read archive member: {name}")
                payload = extracted.read()
            else:
                raise VerificationError(f"non-regular archive member in {source.name}: {name}")
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE if payload is None else tarfile.REGTYPE
            info.size = 0 if payload is None else len(payload)
            info.mode = _canonical_mode(original.mode, executable=payload is None)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            members.append((info, payload))
    members.sort(key=lambda item: item[0].name)
    return members


def canonicalize_sdist(source: Path, epoch: int, output: Path | None = None) -> Path:
    """Rewrite an sdist with deterministic gzip and tar metadata."""
    destination = source if output is None else output
    members = _read_sdist_members(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".canonical.tmp")
    try:
        with (
            temporary.open("wb") as raw,
            gzip.GzipFile(
                filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0
            ) as compressed,
            tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive,
        ):
            for info, payload in members:
                info.mtime = epoch
                archive.addfile(info, None if payload is None else io.BytesIO(payload))
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _read_wheel_members(source: Path) -> list[tuple[str, int, bytes]]:
    """Read and validate every wheel member before any output is written."""
    entries: list[tuple[str, int, bytes]] = []
    seen: set[str] = set()
    with zipfile.ZipFile(source) as archive:
        for zip_info in archive.infolist():
            name = _member_name(zip_info.filename)
            if name in seen:
                raise VerificationError(f"duplicate archive member in {source.name}: {name}")
            seen.add(name)
            unix_mode = (zip_info.external_attr >> 16) & 0o170000
            if unix_mode not in {0, REGULAR_FILE_TYPE, DIRECTORY_TYPE}:
                raise VerificationError(f"non-regular archive member in {source.name}: {name}")
            if zip_info.is_dir():
                entries.append((f"{name}/", CANONICAL_EXEC_MODE, b""))
                continue
            mode = (zip_info.external_attr >> 16) & 0o7777
            entries.append((name, _canonical_mode(mode, executable=False), archive.read(zip_info)))
    entries.sort(key=lambda item: item[0])
    return entries


def canonicalize_wheel(source: Path, epoch: int, output: Path | None = None) -> Path:
    """Rewrite a wheel with deterministic zip metadata."""
    if epoch < ZIP_MINIMUM_EPOCH:
        raise VerificationError(f"source epoch predates the zip timestamp floor: {epoch}")
    destination = source if output is None else output
    entries = _read_wheel_members(source)
    moment = time.gmtime(epoch)
    stamp = (
        moment.tm_year,
        moment.tm_mon,
        moment.tm_mday,
        moment.tm_hour,
        moment.tm_min,
        moment.tm_sec,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".canonical.tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name, mode, payload in entries:
                info = zipfile.ZipInfo(name, date_time=stamp)
                directory = name.endswith("/")
                file_type = DIRECTORY_TYPE if directory else REGULAR_FILE_TYPE
                info.external_attr = (file_type | mode) << 16
                if directory:
                    info.external_attr |= MSDOS_DIRECTORY_FLAG
                info.create_system = ZIP_UNIX_CREATE_SYSTEM
                info.compress_type = zipfile.ZIP_DEFLATED
                info.extra = b""
                archive.writestr(info, payload)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination
