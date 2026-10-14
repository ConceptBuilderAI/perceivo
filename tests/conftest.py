"""Shared fixtures for the perceivo SDK test suite.

Scope: unit + integration tests against the local extension manager.
E2E fixtures (test_org, api_client) are intentionally absent here —
they belong in a separate ``tests/e2e/conftest.py`` once that suite
is wired against a dedicated automated-test organization on dev.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
import tomllib
from pathlib import Path

_SDK_ROOT = Path(__file__).resolve().parent.parent

# Select which ``perceivo`` this suite tests before the first package import.
# PEP 660 editable installs can register finders that resolve submodules from an
# unrelated checkout even when the repository root is first on ``sys.path``.
# Remove those ambient hooks and make this checkout the sole import root.
_REMOVED_EDITABLE_FINDERS: list[str] = []
for _attribute in ("meta_path", "path_hooks"):
    _kept = []
    for _finder in getattr(sys, _attribute):
        if "perceivo" in repr(_finder).lower():
            _REMOVED_EDITABLE_FINDERS.append(f"sys.{_attribute}: {_finder!r}")
        else:
            _kept.append(_finder)
    setattr(sys, _attribute, _kept)
sys.path[:] = [entry for entry in sys.path if "__editable__" not in entry]
sys.path.insert(0, str(_SDK_ROOT))
sys.path_importer_cache.clear()
importlib.invalidate_caches()

from typing import Iterator

import pytest

import perceivo
from perceivo._internal.context import _ExecutionContext

_PACKAGE_ROOT = Path(perceivo.__file__).resolve().parent
if _PACKAGE_ROOT != _SDK_ROOT / "perceivo":
    raise pytest.UsageError(
        f"perceivo resolved from {_PACKAGE_ROOT}, expected {_SDK_ROOT / 'perceivo'}"
    )
if any(Path(path).resolve() != _PACKAGE_ROOT for path in perceivo.__path__):
    raise pytest.UsageError(
        f"perceivo.__path__ contains a tree outside this checkout: {list(perceivo.__path__)}"
    )

_PROJECT = tomllib.loads((_SDK_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
_DECLARED_VERSION = _PROJECT["project"]["version"]
_VERSION_FILE = (_SDK_ROOT / "VERSION").read_text(encoding="utf-8").strip()
_INSTALLED_VERSION = importlib.metadata.version("perceivo")
if len({_DECLARED_VERSION, _VERSION_FILE, _INSTALLED_VERSION}) != 1:
    raise pytest.UsageError(
        "perceivo version mismatch: "
        f"pyproject.toml={_DECLARED_VERSION!r}, VERSION={_VERSION_FILE!r}, "
        f"installed metadata={_INSTALLED_VERSION!r}. Install this checkout before testing."
    )


def pytest_report_header() -> list[str]:
    """State which tree the suite measured, in the run's own output.

    A suite that silently measures the wrong package is the failure this
    guards against, so the answer belongs in the run's own output rather than
    somewhere a reader has to know the trap exists to go looking for it.
    """
    lines = [f"perceivo under test: {perceivo.__file__} ({_INSTALLED_VERSION})"]
    for finder in _REMOVED_EDITABLE_FINDERS:
        lines.append(f"  stripped ambient finder: {finder}")
    return lines


@pytest.fixture(autouse=True)
def isolated_perceivo_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> Path:
    """Point ``PERCEIVO_HOME`` at a fresh directory for every test.

    Every local run writes its history, scratch and registry under the home, and
    without this the home of a test that names no ``perceivo.yaml`` is
    ``<cwd>/.perceivo`` -- the checkout itself. A test that exercises the
    resolution order deletes the variable with ``monkeypatch.delenv``.
    """
    home = tmp_path_factory.mktemp("perceivo-home")
    monkeypatch.setenv("PERCEIVO_HOME", str(home))
    return home


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Empty temp directory for orchestration save/load/run tests."""
    return tmp_path


@pytest.fixture
def cleared_context() -> Iterator[None]:
    """Run inside a fully-empty _ExecutionContext stack.

    Use for tests that exercise the "outside component execution"
    branches (e.g. ``evaluations.label`` becoming a no-op, or
    ``storage.find`` returning ``None``). Cleans up after each test.
    """
    _ExecutionContext.clear_all()
    try:
        yield
    finally:
        _ExecutionContext.clear_all()


@pytest.fixture
def local_context(tmp_path: Path) -> Iterator[object]:
    """Push a real ``LocalExtensionManager`` onto the context stack.

    Use this for tests that need ``_ExecutionContext.get_ext()`` to
    return a working ExtensionManager rather than raise. Filesystem-
    backed services are pointed at ``tmp_path`` so tests don't touch
    user files.
    """
    from perceivo.config import Config, FilesConfig, SecretsConfig
    from perceivo.local.context import LocalExtensionManager

    files_dir = tmp_path / "files"
    org_dir = tmp_path / "org"
    files_dir.mkdir(parents=True, exist_ok=True)
    org_dir.mkdir(parents=True, exist_ok=True)

    config = Config(
        files=FilesConfig(working_dir=str(files_dir), org_dir=str(org_dir)),
        secrets=SecretsConfig(source="inline", inline={}),
    )
    ext = LocalExtensionManager(config)
    _ExecutionContext.set_ext(ext)
    try:
        yield ext
    finally:
        _ExecutionContext.clear()


@pytest.fixture
def stub_ext(monkeypatch) -> object:
    """Push a minimal mock extension manager.

    Useful when a test wants to assert on adapter calls without
    constructing the full LocalExtensionManager. Tests can attach
    attributes (e.g. ``stub_ext.org_files = MagicMock()``) before
    invoking the function under test.
    """

    class _Stub:
        pass

    ext = _Stub()
    _ExecutionContext.set_ext(ext)
    try:
        yield ext
    finally:
        _ExecutionContext.clear()


def pytest_collection_modifyitems(config, items):
    """Apply the declared ``unit``/``integration``/``e2e`` markers by
    location so the markers are actually used (and ``-m unit`` etc. select).

    - ``tests/sdk_campaign/*`` exercise the local mock runtime end-to-end
      against the orchestration corpus -> ``integration``.
    - ``tests/e2e/*`` (reserved; no such suite yet) -> ``e2e``.
    - everything else (pure unit tests) -> ``unit``.
    """
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if "/e2e/" in nodeid:
            item.add_marker(pytest.mark.e2e)
        elif "/sdk_campaign/" in nodeid:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
