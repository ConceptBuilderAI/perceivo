"""``perceivo.__version__`` must never fall back to a hardcoded literal.

``importlib.metadata.version("perceivo")`` raises in every source checkout
(every contributor, every ``git clone`` behind a bug report). A hardcoded
fallback there reports a stale version, so issues arrive tagged against a
release that does not exist. The fallback reads the repo ``VERSION`` file via
:func:`perceivo._resolve_version`.

The branch tests below exercise :func:`perceivo._resolve_version` **in-process**
(monkeypatching, not a subprocess) so coverage.py — running in the pytest
parent — actually records the fallback and sentinel lines. That coverage is what
the CI suite uploads to SonarCloud for the new-code gate.
"""

from __future__ import annotations

import importlib.metadata
import pathlib
import re

import perceivo

_PKG_INIT = pathlib.Path(perceivo.__file__).resolve()
_REPO_ROOT = _PKG_INIT.parent.parent
_VERSION_FILE = _REPO_ROOT / "VERSION"


def _raise_not_found(_name: str):
    raise importlib.metadata.PackageNotFoundError(_name)


def test_no_stale_hardcoded_version_literal():
    src = _PKG_INIT.read_text(encoding="utf-8")
    assert '"0.3.0"' not in src, "the package version must not be a hardcoded literal"


def test_version_file_is_valid_semver():
    v = _VERSION_FILE.read_text(encoding="utf-8").strip()
    assert re.match(r"^\d+\.\d+\.\d+", v), f"VERSION is not semver: {v!r}"


def test_reported_version_matches_VERSION_file():
    assert perceivo.__version__ == _VERSION_FILE.read_text(encoding="utf-8").strip()


def test_resolve_version_uses_installed_metadata():
    """Normal path: distribution metadata is present and wins."""
    assert perceivo._resolve_version() == importlib.metadata.version("perceivo")


def test_resolve_version_falls_back_to_VERSION_file(monkeypatch):
    """Metadata-missing branch: read the repo VERSION file, never a stale
    literal. Exercised in-process so the lines register as covered."""
    monkeypatch.setattr(importlib.metadata, "version", _raise_not_found)
    resolved = perceivo._resolve_version()
    assert resolved == _VERSION_FILE.read_text(encoding="utf-8").strip()
    assert resolved != "0.3.0"


def test_resolve_version_sentinel_when_everything_unreadable(monkeypatch):
    """Both metadata AND the VERSION file are unreadable: return the explicit
    unknown sentinel — honest, never a fabricated release."""
    monkeypatch.setattr(importlib.metadata, "version", _raise_not_found)

    def _raise_read(self, *args, **kwargs):
        raise OSError("VERSION unreadable")

    monkeypatch.setattr(pathlib.Path, "read_text", _raise_read)
    assert perceivo._resolve_version() == "0.0.0+unknown"
