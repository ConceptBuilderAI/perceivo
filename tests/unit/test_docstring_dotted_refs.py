"""Every ``perceivo.<plane>`` named in shipped source is a real plane.

A docstring that teaches ``perceivo.storage.read`` sends the reader to an
attribute that raises ``AttributeError``: the plane was retired, and the name
left behind in prose outlived it. This guard resolves the plane segment of every
``perceivo.<plane>[.<member>...]`` reference in shipped source, by attribute on
the package or by importing the submodule.

Only the plane segment is resolved. Deeper segments are used on purpose for
things that do not exist -- "there is no ``perceivo.managed_service.database.
resolve``" -- and for logger names such as ``perceivo.local.redis``; a retired
plane named as though it were live is the defect this guards.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import perceivo

PACKAGE = Path(perceivo.__file__).resolve().parent
REFERENCE = re.compile(r"(?<![\w./-])perceivo\.([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)*)")
#: File names, not attributes.
FILENAME_TOKENS = frozenset({"perceivo.yaml", "perceivo.mock.yaml"})
#: Vendored upstream bytes, verified by hash and never edited here.
VENDORED = PACKAGE / "_internal" / "signing"


def plane_exists(plane: str) -> bool:
    """Whether ``perceivo.<plane>`` resolves by attribute or submodule import."""
    if hasattr(perceivo, plane):
        return True
    try:
        importlib.import_module(f"perceivo.{plane}")
    except ImportError:
        return False
    return True


def unresolved(text: str) -> list[tuple[int, str]]:
    """``(line, reference)`` for each reference in ``text`` naming no plane."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in REFERENCE.finditer(line):
            if match.group() in FILENAME_TOKENS or plane_exists(match.group(1)):
                continue
            found.append((number, match.group()))
    return found


def _shipped_sources() -> list[Path]:
    return [p for p in sorted(PACKAGE.rglob("*.py")) if VENDORED not in p.parents]


def _scan_package() -> set[tuple[str, int, str]]:
    repo = PACKAGE.parent
    hits: set[tuple[str, int, str]] = set()
    for path in _shipped_sources():
        relative = path.relative_to(repo).as_posix()
        text = path.read_text(encoding="utf-8")
        hits.update((relative, line, ref) for line, ref in unresolved(text))
    return hits


def test_shipped_source_names_only_real_planes() -> None:
    assert _scan_package() == set()


def test_population_is_not_empty() -> None:
    assert len(_shipped_sources()) > 100


def test_planted_retired_plane_is_reported() -> None:
    planted = '"""Read with ``perceivo.storage.read(entry_id)``."""\n'
    assert unresolved(planted) == [(1, "perceivo.storage.read")]


@pytest.mark.parametrize(
    "text",
    [
        "Configure ``perceivo.yaml`` and ``perceivo.mock.yaml``.",
        "Call ``perceivo.files.read`` or ``perceivo.log.info``.",
        "logging.getLogger('perceivo.local.redis')",
        "see _perceivo.storage and my.perceivo.storage",
    ],
)
def test_real_planes_and_filenames_pass(text: str) -> None:
    assert unresolved(text) == []
