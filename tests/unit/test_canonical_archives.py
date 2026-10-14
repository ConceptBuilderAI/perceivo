"""Focused wheel canonicalization controls."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.canonical_archives import VerificationError, canonicalize_wheel

EPOCH = 1_700_000_000


def _wheel(path: Path, *, stamp: tuple[int, int, int, int, int, int], attributes: int) -> Path:
    payloads = {
        "perceivo/__init__.py": b'__version__ = "1.1.1"\n',
        "perceivo/py.typed": b"",
        "perceivo-1.1.1.dist-info/licenses/LICENSE": b"Apache License\n",
        "perceivo-1.1.1.dist-info/licenses/NOTICE": b"Copyright fixture\n",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(payloads, reverse=stamp[0] % 2 == 0):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.external_attr = attributes
            info.create_system = stamp[0] % 3
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payloads[name])
    return path


def test_canonical_wheel_absorbs_metadata_only_differences(tmp_path: Path) -> None:
    left = _wheel(tmp_path / "left.whl", stamp=(1981, 1, 1, 0, 0, 0), attributes=0o100644 << 16)
    right = _wheel(tmp_path / "right.whl", stamp=(2020, 6, 5, 4, 3, 2), attributes=0o100666 << 16)
    assert left.read_bytes() != right.read_bytes()

    canonicalize_wheel(left, EPOCH)
    canonicalize_wheel(right, EPOCH)

    assert left.read_bytes() == right.read_bytes()
    with zipfile.ZipFile(left) as archive:
        assert archive.read("perceivo/__init__.py") == b'__version__ = "1.1.1"\n'
        assert archive.read("perceivo-1.1.1.dist-info/licenses/NOTICE") == b"Copyright fixture\n"
        assert archive.read("perceivo-1.1.1.dist-info/licenses/LICENSE") == b"Apache License\n"
        for member in archive.infolist():
            assert member.create_system == 3
            assert member.extra == b""
            assert (member.external_attr >> 16) & 0o7777 == 0o644


def test_canonical_wheel_rejects_a_symlink_member(tmp_path: Path) -> None:
    path = tmp_path / "hostile.whl"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("perceivo/link.py", date_time=(1981, 1, 1, 0, 0, 0))
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"../../../etc/shadow")
    before = path.read_bytes()

    with pytest.raises(VerificationError, match="non-regular archive member"):
        canonicalize_wheel(path, EPOCH)
    assert path.read_bytes() == before


def test_canonical_wheel_rejects_an_epoch_below_the_zip_floor(tmp_path: Path) -> None:
    path = _wheel(tmp_path / "wheel.whl", stamp=(1981, 1, 1, 0, 0, 0), attributes=0o100644 << 16)
    before = path.read_bytes()

    with pytest.raises(VerificationError, match="zip timestamp floor"):
        canonicalize_wheel(path, 0)
    assert path.read_bytes() == before
