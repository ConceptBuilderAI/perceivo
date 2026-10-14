"""The public ``perceivo`` distribution ships only the authoring SDK package.

The package finder once used a ``perceivo*`` glob, which also matches similarly
named internal packages. These tests bind the real setuptools discovery config
and the source archive manifest to the intended public package boundary.
"""

import subprocess
import tomllib
from pathlib import Path
from typing import TypedDict, TypeGuard

import pytest
from setuptools.discovery import PackageFinder

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERNAL_PACKAGE = "perceivo_platform"
PUBLIC_PACKAGE = "perceivo"


class _FindConfig(TypedDict):
    where: list[str]
    include: list[str]
    exclude: list[str]


def _is_str_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


@pytest.fixture(scope="module")
def find_config() -> _FindConfig:
    """Return the root pyproject's package-discovery table."""
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    raw: object = metadata["tool"]["setuptools"]["packages"]["find"]
    assert isinstance(raw, dict), f"setuptools package discovery is not a table: {raw!r}"
    where = raw.get("where", ["."])
    include = raw.get("include", ["*"])
    exclude = raw.get("exclude", [])
    assert _is_str_list(where), f"setuptools find.where is not a string list: {where!r}"
    assert _is_str_list(include), f"setuptools find.include is not a string list: {include!r}"
    assert _is_str_list(exclude), f"setuptools find.exclude is not a string list: {exclude!r}"
    return {"where": where, "include": include, "exclude": exclude}


@pytest.fixture(scope="module")
def discovered_packages(find_config: _FindConfig) -> list[str]:
    """Run the same package discovery used by the build backend."""
    packages: list[str] = []
    for where in find_config.get("where", ["."]):
        packages.extend(
            PackageFinder.find(
                where=str(REPO_ROOT / where),
                include=tuple(find_config.get("include", ("*",))),
                exclude=tuple(find_config.get("exclude", ())),
            )
        )
    return packages


def test_root_wheel_ships_the_public_package(discovered_packages: list[str]) -> None:
    assert PUBLIC_PACKAGE in discovered_packages, (
        f"The distribution no longer ships `{PUBLIC_PACKAGE}`. "
        f"Discovered: {sorted(discovered_packages)}"
    )
    assert any(name.startswith(f"{PUBLIC_PACKAGE}.") for name in discovered_packages), (
        f"No `{PUBLIC_PACKAGE}.*` subpackages discovered: {sorted(discovered_packages)}"
    )


def test_root_wheel_does_not_ship_internal_platform_package(
    discovered_packages: list[str],
) -> None:
    leaked = sorted(
        name
        for name in discovered_packages
        if name == INTERNAL_PACKAGE or name.startswith(f"{INTERNAL_PACKAGE}.")
    )
    assert not leaked, (
        f"The public wheel would ship internal modules {leaked}. Check "
        "`[tool.setuptools.packages.find]` in pyproject.toml: `include` must be exact "
        "(`perceivo`, `perceivo.*`), and `where` must contain only `.`."
    )


def test_find_include_is_exact_and_not_a_glob(find_config: dict) -> None:
    """A broad prefix glob can silently include similarly named packages."""
    include = find_config.get("include", [])
    assert include == [PUBLIC_PACKAGE, f"{PUBLIC_PACKAGE}.*"], (
        f"Expected an exact include of ['{PUBLIC_PACKAGE}', '{PUBLIC_PACKAGE}.*'], "
        f"got {include}. A `{PUBLIC_PACKAGE}*` glob also matches `{INTERNAL_PACKAGE}`."
    )


def test_find_has_no_extra_where_root(find_config: dict) -> None:
    """A second discovery root can re-expose packages outside the SDK."""
    where = find_config.get("where", ["."])
    assert where == ["."], (
        f"Expected a single discovery root ['.'], got {where}. An extra root can "
        f"re-expose `{INTERNAL_PACKAGE}` in the public wheel."
    )


def test_manifest_does_not_pull_internal_package_into_the_sdist() -> None:
    """MANIFEST.in must preserve the same package boundary for the sdist."""
    manifest = REPO_ROOT / "MANIFEST.in"
    if not manifest.is_file():
        return
    offending = [
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#") and INTERNAL_PACKAGE in line
    ]
    assert not offending, f"MANIFEST.in would include internal files via {offending}."


def test_the_vendored_template_library_is_package_data() -> None:
    """Every ``perceivo/templates/_library`` file ships in the wheel and the sdist.

    ``perceivo.templates`` reads these files at run time, so a bundle the
    package-data globs miss is a template ``use()`` cannot find once installed.
    """
    import fnmatch

    library = REPO_ROOT / PUBLIC_PACKAGE / "templates" / "_library"
    files = sorted(p.relative_to(REPO_ROOT / PUBLIC_PACKAGE).as_posix() for p in library.iterdir())
    assert len(files) == 24, files  # 23 bundles + SOURCE.json

    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    globs = metadata["tool"]["setuptools"]["package-data"][PUBLIC_PACKAGE]
    missed = [f for f in files if not any(fnmatch.fnmatchcase(f, g) for g in globs)]
    assert not missed, f"not package data: {missed}"

    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "recursive-include perceivo/templates/_library *.json" in manifest


# --- the vendored request signer ships -----------------------------------------------

_SIGNING_DATA = ("perceivo/_internal/signing/vectors/hmac_v1_vectors.json",)
#: The base runtime dependencies. Vendoring the signer added none (a public 1.0.0
#: may not depend on a private package, and Entra tokens need only httpx).
_BASE_DEPENDENCIES = [
    "httpx>=0.27.0",
    "pyyaml>=6.0",
    "click>=8.0",
    "pydantic>=2.0",
]

_LOAD_IN_FRESH_VENV = r"""
import importlib.util, pathlib, sys, sysconfig, types
site = pathlib.Path(sysconfig.get_paths()["purelib"])
assert importlib.util.find_spec("perceivo_client") is None, "perceivo_client is importable"
# The base dependencies are deliberately absent, so bind the two parent packages
# to the installed directories without running their __init__ imports.
for name in ("perceivo", "perceivo._internal"):
    module = types.ModuleType(name)
    module.__path__ = [str(site.joinpath(*name.split(".")))]
    sys.modules[name] = module
from perceivo._internal.signing import load_golden_vectors
table = load_golden_vectors()
assert len(table["vectors"]) == 36, len(table["vectors"])
print("WHEEL-LOAD-OK")
"""


def test_base_dependencies_are_unchanged() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["dependencies"] == _BASE_DEPENDENCIES


def test_signing_data_is_package_data() -> None:
    import fnmatch

    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    globs = metadata["tool"]["setuptools"]["package-data"][PUBLIC_PACKAGE]
    for path in _SIGNING_DATA:
        relative = path.removeprefix(f"{PUBLIC_PACKAGE}/")
        assert any(fnmatch.fnmatchcase(relative, g) for g in globs), path


def _copy_tree(source: Path, drop: tuple[str, ...] = ()) -> None:
    """Copy what the build reads, so the build's own output lands outside the repo."""
    import shutil

    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "MANIFEST.in"):
        if name not in drop and (REPO_ROOT / name).exists():
            shutil.copy2(REPO_ROOT / name, source / name)
    shutil.copytree(
        REPO_ROOT / PUBLIC_PACKAGE,
        source / PUBLIC_PACKAGE,
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def _build(source: Path, out: Path, hook: str) -> "subprocess.CompletedProcess[str]":
    import subprocess
    import sys

    # One build per process: setuptools keeps state between hooks in one process.
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; from setuptools import build_meta; build_meta.{hook}(sys.argv[1])",
            str(out),
        ],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict[str, Path]:
    """Build the wheel and the sdist from a copy of the tree (the build writes
    ``build/`` and ``*.egg-info`` next to the sources)."""
    source = tmp_path_factory.mktemp("src")
    _copy_tree(source)
    out = tmp_path_factory.mktemp("dist")
    for hook in ("build_wheel", "build_sdist"):
        proc = _build(source, out, hook)
        assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return {
        "wheel": next(out.glob("*.whl")),
        "sdist": next(out.glob("*.tar.gz")),
    }


def test_the_wheel_and_the_sdist_carry_the_signing_data(built) -> None:
    import tarfile
    import zipfile

    wheel_names = set(zipfile.ZipFile(built["wheel"]).namelist())
    with tarfile.open(built["sdist"]) as sdist:
        sdist_names = {name.split("/", 1)[1] for name in sdist.getnames() if "/" in name}
    for path in _SIGNING_DATA:
        assert path in wheel_names, path
        assert path in sdist_names, path


def test_the_wheel_loads_its_vectors_in_a_fresh_venv_without_perceivo_client(
    built, tmp_path
) -> None:
    import subprocess
    import sys
    import venv

    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(built["wheel"])],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert install.returncode == 0, install.stdout[-3000:] + install.stderr[-3000:]
    proc = subprocess.run(
        [str(python), "-c", _LOAD_IN_FRESH_VENV],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WHEEL-LOAD-OK" in proc.stdout


def test_pip_check_is_clean_in_the_test_environment() -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# License metadata (SPDX expression, license files, NOTICE)
# --------------------------------------------------------------------------- #

LICENSE_METADATA = (
    "Metadata-Version: 2.4",
    "License-Expression: Apache-2.0",
    "License-File: LICENSE",
    "License-File: NOTICE",
    "Project-URL: Changelog, https://github.com/ConceptBuilderAI/perceivo/blob/main/CHANGELOG.md",
)


def _license_problems(wheel: Path) -> list[str]:
    """Every way the wheel's license metadata or license files are wrong."""
    import zipfile

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        lines = archive.read(metadata_name).decode("utf-8").splitlines()
    problems = [f"METADATA lacks {line!r}" for line in LICENSE_METADATA if line not in lines]
    problems += [
        f"METADATA keeps {line!r}" for line in lines if line.startswith("Classifier: License ::")
    ]
    for legal in ("LICENSE", "NOTICE"):
        if not any(name.endswith(f".dist-info/licenses/{legal}") for name in names):
            problems.append(f"wheel lacks dist-info/licenses/{legal}")
    return problems


def test_the_wheel_carries_spdx_license_metadata_and_both_license_files(built) -> None:
    assert _license_problems(built["wheel"]) == []


def test_the_sdist_carries_both_license_files(built) -> None:
    import tarfile

    with tarfile.open(built["sdist"]) as sdist:
        names = {name.split("/", 1)[1] for name in sdist.getnames() if "/" in name}
    assert {"LICENSE", "NOTICE"} <= names


def test_a_missing_notice_cannot_ship(tmp_path: Path) -> None:
    """The build alone is not the control: setuptools may build without NOTICE."""
    source, out = tmp_path / "src", tmp_path / "dist"
    source.mkdir()
    out.mkdir()
    _copy_tree(source, drop=("NOTICE",))
    proc = _build(source, out, "build_wheel")
    if proc.returncode == 0:
        problems = _license_problems(next(out.glob("*.whl")))
        assert "wheel lacks dist-info/licenses/NOTICE" in problems
        assert "METADATA lacks 'License-File: NOTICE'" in problems


def test_a_license_classifier_fails_the_build(tmp_path: Path) -> None:
    source, out = tmp_path / "src", tmp_path / "dist"
    source.mkdir()
    out.mkdir()
    _copy_tree(source)
    pyproject = source / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    anchor = '    "Intended Audience :: Developers",\n'
    assert text.count(anchor) == 1
    pyproject.write_text(
        text.replace(
            anchor, anchor + '    "License :: OSI Approved :: Apache Software License",\n'
        ),
        encoding="utf-8",
    )
    proc = _build(source, out, "build_wheel")
    assert proc.returncode != 0
    assert "License classifiers have been superseded" in proc.stdout + proc.stderr


def test_notice_is_the_license_file_it_claims_to_be() -> None:
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright 2026 ConceptBuilder AI" in notice
    assert "Licensed under the Apache License, Version 2.0" in notice
