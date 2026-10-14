from __future__ import annotations

import ast
import gzip
import io
import json
import os
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Callable

import pytest

import perceivo
from scripts.canonical_archives import (
    VerificationError,
    canonicalize_sdist,
)
from scripts.canonical_archives import (
    resolve_epoch as _resolve_epoch,
)
from scripts.verify_public_tree import (
    MANIFEST_NAME,
    SDK_EMITTED_VAL_CODES,
    compare_archive_sets,
    seal_repository,
    verify_archives,
    verify_repository,
)

# A fixed instant after the 1980 zip timestamp floor, used for every
# canonicalization fixture so expected bytes never depend on the clock.
EPOCH = 1_700_000_000


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Public Verification Test",
        "GIT_AUTHOR_EMAIL": "verify@example.invalid",
        "GIT_COMMITTER_NAME": "Public Verification Test",
        "GIT_COMMITTER_EMAIL": "verify@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8").strip()


def _write(root: Path, name: str, data: bytes) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _seed(path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "exporter": {"name": "fixture", "version": "1.0.0"},
        "inputs": {"source_commit": "0" * 40, "destination_commit": "1" * 40},
        "selectors": {},
        "transformations": ["fixture"],
        "files": [],
        "tree_digest": "0" * 64,
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "core.autocrlf", "false")
    _write(root, "perceivo/__init__.py", b'__version__ = "1.1.1"\n')
    _write(root, "perceivo/py.typed", b"")
    _write(root, "LICENSE", b"Apache License\n")
    _write(root, "NOTICE", b"Copyright fixture\n")
    return root


def _seal_and_commit(root: Path, tmp_path: Path) -> None:
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")
    seal_repository(root, seed, root / MANIFEST_NAME)
    _git(root, "add", MANIFEST_NAME)
    _git(root, "commit", "-m", "fixture")


def _archives(root: Path, directory: Path, include_notice: bool = True) -> tuple[Path, Path]:
    directory.mkdir()
    wheel = directory / "perceivo-1.1.1-py3-none-any.whl"
    wheel_files = {
        "perceivo/__init__.py": (root / "perceivo/__init__.py").read_bytes(),
        "perceivo/py.typed": b"",
        "perceivo-1.1.1.dist-info/licenses/LICENSE": (root / "LICENSE").read_bytes(),
    }
    if include_notice:
        wheel_files["perceivo-1.1.1.dist-info/licenses/NOTICE"] = (root / "NOTICE").read_bytes()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in wheel_files.items():
            archive.writestr(name, data)

    sdist = directory / "perceivo-1.1.1.tar.gz"
    sdist_files = {
        "perceivo-1.1.1/perceivo/__init__.py": (root / "perceivo/__init__.py").read_bytes(),
        "perceivo-1.1.1/perceivo/py.typed": b"",
        "perceivo-1.1.1/LICENSE": (root / "LICENSE").read_bytes(),
    }
    if include_notice:
        sdist_files["perceivo-1.1.1/NOTICE"] = (root / "NOTICE").read_bytes()
    with tarfile.open(sdist, "w:gz") as archive:
        for name, data in sdist_files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return wheel, sdist


def test_seals_and_verifies_closed_tracked_tree(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _seal_and_commit(root, tmp_path)

    manifest = verify_repository(root)

    paths = {item["path"] for item in manifest["files"]}
    assert paths == {"LICENSE", "NOTICE", "perceivo/__init__.py", "perceivo/py.typed"}
    assert manifest["manifest_exclusion"].startswith(MANIFEST_NAME)


def test_seals_and_verifies_without_commit_pins(tmp_path: Path) -> None:
    """A published tree may omit commit pins whose commits are not public."""
    root = _repository(tmp_path)
    seed = tmp_path / "seed.json"
    _seed(seed)
    manifest = json.loads(seed.read_text(encoding="utf-8"))
    manifest["inputs"] = {}
    seed.write_text(json.dumps(manifest), encoding="utf-8")
    _git(root, "add", "-A")
    seal_repository(root, seed, root / MANIFEST_NAME)
    _git(root, "add", MANIFEST_NAME)
    _git(root, "commit", "-m", "fixture")

    assert verify_repository(root)["inputs"] == {}


def test_rejects_a_malformed_commit_pin(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    seed = tmp_path / "seed.json"
    _seed(seed)
    manifest = json.loads(seed.read_text(encoding="utf-8"))
    manifest["inputs"] = {"source_commit": "abc123"}
    seed.write_text(json.dumps(manifest), encoding="utf-8")
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="source_commit is not a full SHA-1"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_rejects_tracked_file_missing_from_manifest(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _seal_and_commit(root, tmp_path)
    _write(root, "unexpected.txt", b"unexpected\n")
    _git(root, "add", "unexpected.txt")
    _git(root, "commit", "-m", "unmanifested")

    with pytest.raises(VerificationError, match="unmanifested"):
        verify_repository(root)


def test_rejects_changed_working_bytes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _seal_and_commit(root, tmp_path)
    _write(root, "perceivo/__init__.py", b"changed = True\n")

    with pytest.raises(VerificationError, match="hash mismatch"):
        verify_repository(root)


def test_rejects_denied_content_before_sealing(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    marker = ("TI" + "CK-999").encode()
    _write(root, "perceivo/private.py", b"# " + marker + b"\n")
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="private ticket identifier"):
        seal_repository(root, seed, root / MANIFEST_NAME)


@pytest.mark.parametrize("suffix", ["ET-999", "ET 999", "_999", "#999", "ET999", "et-999"])
def test_rejects_each_spelling_of_a_ticket_identifier(tmp_path: Path, suffix: str) -> None:
    root = _repository(tmp_path)
    _write(root, "perceivo/private.py", b"# see " + ("TI" + "CK" + suffix).encode() + b"\n")
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="private ticket identifier"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_allows_a_word_that_ends_in_the_ticket_letters(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "perceivo/private.py", b"# lipstick-999\n")
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    seal_repository(root, seed, root / MANIFEST_NAME)


def test_allows_words_that_merely_contain_the_substrate_letters(tmp_path: Path) -> None:
    """Two-answer control, half one: `array` and `gray` share letters with the
    banned substrate name but are not the word itself, and must pass."""
    root = _repository(tmp_path)
    _write(
        root,
        "examples/orchestrations/sample-package/benign.py",
        b"# an array of items, painted gray\n",
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    seal_repository(root, seed, root / MANIFEST_NAME)  # must not raise


def test_rejects_the_execution_substrate_name(tmp_path: Path) -> None:
    """Two-answer control, half two: the bare word-boundary substrate name
    must fail, built from fragments here too so this test cannot be the leak
    it is proving is denied."""
    root = _repository(tmp_path)
    substrate = "r" + "ay"
    _write(
        root,
        "examples/orchestrations/sample-package/leak.py",
        f"# runs on {substrate} clusters\n".encode(),
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="execution substrate name"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_rejects_internal_pitfalls_catalogue_reference(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root, "examples/orchestrations/sample-package/leak.py", b"# see Trap 42 for background\n"
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="internal pitfalls-catalogue reference"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_allows_a_validator_rule_code_the_sdk_actually_emits(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "examples/orchestrations/sample-package/benign.py",
        b"# raises VAL-CONTRACT-RESP-001 on an undeclared field\n",
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    seal_repository(root, seed, root / MANIFEST_NAME)  # must not raise


def test_rejects_a_validator_rule_code_the_sdk_does_not_emit(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "examples/orchestrations/sample-package/leak.py",
        b"# raises VAL-COMP-005 internally\n",
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="unshipped validator rule code"):
        seal_repository(root, seed, root / MANIFEST_NAME)


#: Tokens inside an allowed family, or extending an allowed code, that the
#: SDK never emits. Built from fragments so this table is not itself the leak.
_VAL_FAMILY_OVERREACH: tuple[bytes, ...] = (
    b"VAL" + b"-CONTRACT-RESP-XYZ",
    b"VAL" + b"-CONTRACT-RESP-OUT-001",
    b"VAL" + b"-SECRET-001-X",
    b"VAL" + b"-ENTRY-CLASS",
    b"VAL" + b"-ENTRY-CLASS-001",
    b"VAL" + b"-SCRIPT-EMPTY-001",
    b"VAL" + b"-TYPE-001x",
    b"VAL" + b"-MAINFLOW-OUT-001-002",
)


@pytest.mark.parametrize("token", _VAL_FAMILY_OVERREACH)
def test_rejects_a_longer_token_inside_an_allowed_family(tmp_path: Path, token: bytes) -> None:
    root = _repository(tmp_path)
    _write(root, "examples/orchestrations/sample-package/leak.py", b"# raises " + token + b"\n")
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="unshipped validator rule code"):
        seal_repository(root, seed, root / MANIFEST_NAME)


@pytest.mark.parametrize(
    "token",
    (
        b"VAL-ENTRY",
        b"VAL-TYPE-001",
        b"VAL-CONTRACT-RESP-002",
        b"VAL-CONTRACT-OPTIONAL-001",
        b"VAL-UNVERIFIABLE-MANIFEST-001",
        b"VAL-SECRET-001",
        b"VAL-MAINFLOW-OUT-001",
    ),
)
def test_allows_each_exact_code_shape_the_sdk_emits(tmp_path: Path, token: bytes) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "examples/orchestrations/sample-package/benign.py",
        b"# raises " + token + b". See `" + token + b"`\n",
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    seal_repository(root, seed, root / MANIFEST_NAME)  # must not raise


def _emitted_val_codes(validator_dir: Path) -> set[str]:
    """Rule codes the validator source emits: a ``VAL-`` first argument or a ``[VAL-...]`` token."""
    bracketed = re.compile(r"\[VAL-([A-Z]+(?:-[A-Z]+)*-\d{3})\]")
    codes: set[str] = set()
    for source in sorted(validator_dir.rglob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("VAL-")
            ):
                codes.add(node.args[0].value.removeprefix("VAL-"))
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                codes.update(bracketed.findall(node.value))
    return codes


def test_allowed_validator_codes_are_the_ones_the_installed_sdk_emits() -> None:
    validator_dir = Path(perceivo.__file__).resolve().parent / "validator"
    emitted = _emitted_val_codes(validator_dir)
    assert emitted, f"no rule codes found under {validator_dir}"
    assert sorted(SDK_EMITTED_VAL_CODES) == sorted(emitted)
    assert len(set(SDK_EMITTED_VAL_CODES)) == len(SDK_EMITTED_VAL_CODES)


def test_rejects_the_internal_documentation_repository_name(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    docs_repo = "perceivo" + "-documentation"
    _write(
        root,
        "examples/orchestrations/sample-package/leak.py",
        f"# see {docs_repo} for the design\n".encode(),
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="internal documentation repository name"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_rejects_an_agent_pipeline_reference(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "examples/orchestrations/sample-package/leak.py",
        ("# filed under " + "Agent" + " Pipeline process docs\n").encode(),
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="internal process document reference"):
        seal_repository(root, seed, root / MANIFEST_NAME)


@pytest.mark.parametrize(
    "template",
    ["# built on the {} internally\n", "# see {}_corpus/sample.json\n"],
)
def test_rejects_the_internal_codename(tmp_path: Path, template: str) -> None:
    root = _repository(tmp_path)
    codename = "lex" + "ograph"
    _write(
        root,
        "examples/orchestrations/sample-package/leak.py",
        template.format(codename).encode(),
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="internal codename"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_rejects_the_internal_component_reference(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "examples/orchestrations/sample-package/leak.py",
        b"# routed through execution-graph-classifier\n",
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="internal component reference"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_allows_the_word_benchmark(tmp_path: Path) -> None:
    """`bench` is denied as a whole word; `benchmark` must still pass."""
    root = _repository(tmp_path)
    _write(
        root,
        "examples/orchestrations/sample-package/benign.py",
        b"# see the benchmark suite for numbers\n",
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    seal_repository(root, seed, root / MANIFEST_NAME)  # must not raise


def test_rejects_the_word_bench(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "examples/orchestrations/sample-package/leak.py",
        b"# run against the internal bench harness\n",
    )
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="internal bench/trap reference"):
        seal_repository(root, seed, root / MANIFEST_NAME)


#: One line per internal-vocabulary rule, each carrying the text that rule
#: refuses, built from fragments so this table is not itself the leak.
_PACKAGE_SCOPED_LEAKS: tuple[tuple[str, bytes], ...] = (
    ("execution substrate name", b"# runs on " + b"r" + b"ay" + b" clusters\n"),
    ("internal pitfalls-catalogue reference", b"# see Trap 42 for background\n"),
    ("unshipped validator rule code", b"# raises VAL" + b"-COMP-005\n"),
    ("internal documentation repository name", b"# see perceivo" + b"-documentation\n"),
    ("internal process document reference", b"# filed under Agent" + b" Pipeline\n"),
    ("internal codename", b"# built on the lex" + b"ograph\n"),
    ("internal component reference", b"# via execution-graph" + b"-classifier\n"),
    ("internal bench/trap reference", b"# the internal be" + b"nch harness\n"),
)


@pytest.mark.parametrize(("label", "leak"), _PACKAGE_SCOPED_LEAKS)
def test_the_vocabulary_rules_refuse_the_text_inside_a_package(
    tmp_path: Path, label: str, leak: bytes
) -> None:
    """Half one of the scoping control: inside a package, every rule bites."""
    root = _repository(tmp_path)
    _write(root, "examples/orchestrations/sample-package/leak.py", leak)
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match=re.escape(label)):
        seal_repository(root, seed, root / MANIFEST_NAME)


@pytest.mark.parametrize(("label", "leak"), _PACKAGE_SCOPED_LEAKS)
@pytest.mark.parametrize("vendored", ("perceivo/vendored.py", "tests/unit/test_vendored.py"))
def test_the_vocabulary_rules_do_not_reach_the_vendored_sdk(
    tmp_path: Path, label: str, leak: bytes, vendored: str
) -> None:
    """Half two: the identical text is ACCEPTED outside the packages.

    ``perceivo/`` and ``tests/unit/`` are copied byte-for-byte from an SDK
    release, so a word this repository dislikes there is an intake item for the
    SDK. Without this half, a scoping that quietly stopped applying anywhere
    would look exactly like a scoping that works.
    """
    root = _repository(tmp_path)
    _write(root, vendored, leak)
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    seal_repository(root, seed, root / MANIFEST_NAME)  # must not raise


def test_the_universal_rules_still_reach_the_vendored_sdk(tmp_path: Path) -> None:
    """Scoping the vocabulary rules must not scope the secret-class rules.

    The ticket, claim-marker, repository-URL, key and token rules hold
    everywhere; only the internal-vocabulary rules are package-scoped.
    """
    root = _repository(tmp_path)
    marker = b"TI" + b"CK-4242"
    _write(root, "perceivo/vendored.py", b"# " + marker + b"\n")
    seed = tmp_path / "seed.json"
    _seed(seed)
    _git(root, "add", "-A")

    with pytest.raises(VerificationError, match="private ticket identifier"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_rejects_symlink_mode_without_platform_symlink_support(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    object_id = _git(root, "hash-object", "-w", "--stdin", input_bytes=b"__init__.py")
    _git(
        root,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{object_id},perceivo/link.py",
    )
    seed = tmp_path / "seed.json"
    _seed(seed)

    with pytest.raises(VerificationError, match="unsupported Git mode"):
        seal_repository(root, seed, root / MANIFEST_NAME)


def test_rejects_second_perceivo_package_boundary(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "perceivo-extra/__init__.py", b"")
    _seal_and_commit(root, tmp_path)

    with pytest.raises(VerificationError, match="invalid package boundary"):
        verify_repository(root)


def test_verifies_archive_members_and_reproducibility(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _seal_and_commit(root, tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    wheel, sdist = _archives(root, first)
    _archives(root, second)

    verify_archives(root, wheel, sdist)
    compare_archive_sets(first, second)


def test_rejects_archive_without_notice(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _seal_and_commit(root, tmp_path)
    wheel, sdist = _archives(root, tmp_path / "archives", include_notice=False)

    with pytest.raises(VerificationError, match="NOTICE absent"):
        verify_archives(root, wheel, sdist)


SDIST_PAYLOADS = {
    "perceivo-1.1.1/perceivo/__init__.py": b'__version__ = "1.1.1"\n',
    "perceivo-1.1.1/LICENSE": b"Apache License\n",
    "perceivo-1.1.1/NOTICE": b"Copyright fixture\n",
    "perceivo-1.1.1/scripts/release.sh": b"#!/bin/sh\necho release\n",
}
EXECUTABLE_MEMBER = "perceivo-1.1.1/scripts/release.sh"
PACKAGE_MEMBER = "perceivo-1.1.1/perceivo/__init__.py"


def _tar_bytes(build: Callable[[tarfile.TarFile], None]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w|", format=tarfile.PAX_FORMAT) as archive:
        build(archive)
    return buffer.getvalue()


def _gzip_bytes(payload: bytes, *, mtime: int, filename: str) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(filename=filename, mode="wb", fileobj=buffer, mtime=mtime) as compressed:
        compressed.write(payload)
    return buffer.getvalue()


def _sdist(
    path: Path,
    *,
    mtime: int = 0,
    mode: int = 0o644,
    owner: int = 0,
    owner_name: str = "",
    gzip_mtime: int = 0,
    gzip_filename: str = "",
    reverse: bool = False,
    executable: tuple[str, ...] = (),
) -> Path:
    """Write an sdist whose payloads are fixed and whose metadata varies."""

    def build(archive: tarfile.TarFile) -> None:
        for name in sorted(SDIST_PAYLOADS, reverse=reverse):
            data = SDIST_PAYLOADS[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = mtime
            info.mode = 0o700 if name in executable else mode
            info.uid = info.gid = owner
            info.uname = info.gname = owner_name
            archive.addfile(info, io.BytesIO(data))

    tar = _tar_bytes(build)
    path.write_bytes(_gzip_bytes(tar, mtime=gzip_mtime, filename=gzip_filename))
    return path


def _hostile_sdist(path: Path, build: Callable[[tarfile.TarFile], None]) -> Path:
    """Write an sdist that carries one benign member and one hostile member."""

    def compose(archive: tarfile.TarFile) -> None:
        payload = SDIST_PAYLOADS[PACKAGE_MEMBER]
        good = tarfile.TarInfo(PACKAGE_MEMBER)
        good.size = len(payload)
        archive.addfile(good, io.BytesIO(payload))
        build(archive)

    path.write_bytes(_gzip_bytes(_tar_bytes(compose), mtime=0, filename=""))
    return path


def _members(path: Path) -> dict[str, tuple[bytes, int, int, int, str, str, int]]:
    collected: dict[str, tuple[bytes, int, int, int, str, str, int]] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            extracted = archive.extractfile(member)
            payload = b"" if extracted is None else extracted.read()
            collected[member.name] = (
                payload,
                member.mode,
                member.uid,
                member.gid,
                member.uname,
                member.gname,
                int(member.mtime),
            )
    return collected


def _reject(path: Path, message: str) -> None:
    """Assert the archive is rejected and left byte-identical on disk."""
    before = path.read_bytes()
    with pytest.raises(VerificationError, match=message):
        canonicalize_sdist(path, EPOCH)
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.canonical.tmp"))


def test_canonical_sdist_absorbs_metadata_only_differences(tmp_path: Path) -> None:
    left = _sdist(tmp_path / "left.tar.gz")
    right = _sdist(
        tmp_path / "right.tar.gz",
        mtime=1_600_000_000,
        mode=0o666,
        owner=501,
        owner_name="builder",
        gzip_mtime=1_234_567,
        gzip_filename="right.tar",
        reverse=True,
    )
    assert left.read_bytes() != right.read_bytes()

    canonicalize_sdist(left, EPOCH)
    canonicalize_sdist(right, EPOCH)

    assert left.read_bytes() == right.read_bytes()


def test_canonical_sdist_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    source = _sdist(tmp_path / "source.tar.gz", mtime=42, mode=0o666, owner=7, owner_name="ci")
    first = canonicalize_sdist(source, EPOCH, tmp_path / "first.tar.gz").read_bytes()
    second = canonicalize_sdist(source, EPOCH, tmp_path / "second.tar.gz").read_bytes()
    assert first == second

    in_place = tmp_path / "in-place.tar.gz"
    in_place.write_bytes(source.read_bytes())
    canonicalize_sdist(in_place, EPOCH)
    once = in_place.read_bytes()
    canonicalize_sdist(in_place, EPOCH)
    assert in_place.read_bytes() == once == first


def test_canonical_sdist_preserves_payloads_and_legal_files(tmp_path: Path) -> None:
    source = _sdist(tmp_path / "source.tar.gz", mtime=99, mode=0o666, owner=501, owner_name="who")
    canonicalize_sdist(source, EPOCH)

    members = _members(source)
    assert set(members) == set(SDIST_PAYLOADS)
    for name, payload in SDIST_PAYLOADS.items():
        assert members[name][0] == payload
    for legal in ("perceivo-1.1.1/LICENSE", "perceivo-1.1.1/NOTICE"):
        assert members[legal][1:] == (0o644, 0, 0, "", "", EPOCH)


def test_canonical_sdist_preserves_the_executable_bit(tmp_path: Path) -> None:
    source = _sdist(tmp_path / "source.tar.gz", mode=0o666, executable=(EXECUTABLE_MEMBER,))
    canonicalize_sdist(source, EPOCH)

    members = _members(source)
    assert members[EXECUTABLE_MEMBER][1] == 0o755
    assert members["perceivo-1.1.1/LICENSE"][1] == 0o644


def test_canonical_sdist_rejects_an_absolute_member(tmp_path: Path) -> None:
    def build(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("/etc/shadow")
        info.size = 0
        archive.addfile(info, io.BytesIO(b""))

    _reject(_hostile_sdist(tmp_path / "hostile.tar.gz", build), "unsafe path")


def test_canonical_sdist_rejects_a_parent_traversal_member(tmp_path: Path) -> None:
    def build(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("perceivo-1.1.1/../../escape.py")
        info.size = 0
        archive.addfile(info, io.BytesIO(b""))

    _reject(_hostile_sdist(tmp_path / "hostile.tar.gz", build), "unsafe path segments")


def test_canonical_sdist_rejects_a_drive_qualified_member(tmp_path: Path) -> None:
    def build(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("C:/Windows/system32/evil.py")
        info.size = 0
        archive.addfile(info, io.BytesIO(b""))

    _reject(_hostile_sdist(tmp_path / "hostile.tar.gz", build), "unsafe drive-qualified path")


def test_canonical_sdist_rejects_a_symlink_member(tmp_path: Path) -> None:
    def build(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("perceivo-1.1.1/link.py")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../../etc/shadow"
        archive.addfile(info)

    _reject(_hostile_sdist(tmp_path / "hostile.tar.gz", build), "link archive member")


def test_canonical_sdist_rejects_a_hardlink_member(tmp_path: Path) -> None:
    def build(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("perceivo-1.1.1/hard.py")
        info.type = tarfile.LNKTYPE
        info.linkname = PACKAGE_MEMBER
        archive.addfile(info)

    _reject(_hostile_sdist(tmp_path / "hostile.tar.gz", build), "link archive member")


def test_canonical_sdist_rejects_a_device_member(tmp_path: Path) -> None:
    def build(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("perceivo-1.1.1/console")
        info.type = tarfile.CHRTYPE
        info.devmajor = 5
        info.devminor = 1
        archive.addfile(info)

    _reject(_hostile_sdist(tmp_path / "hostile.tar.gz", build), "device archive member")


def test_canonical_sdist_rejects_a_fifo_member(tmp_path: Path) -> None:
    def build(archive: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("perceivo-1.1.1/pipe")
        info.type = tarfile.FIFOTYPE
        archive.addfile(info)

    _reject(_hostile_sdist(tmp_path / "hostile.tar.gz", build), "device archive member")


def test_canonical_sdist_rejects_a_duplicate_member(tmp_path: Path) -> None:
    def build(archive: tarfile.TarFile) -> None:
        payload = SDIST_PAYLOADS[PACKAGE_MEMBER]
        info = tarfile.TarInfo(PACKAGE_MEMBER)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    _reject(_hostile_sdist(tmp_path / "hostile.tar.gz", build), "duplicate archive member")


def test_canonical_sdist_rejects_a_negative_source_epoch(tmp_path: Path) -> None:
    source = _sdist(tmp_path / "source.tar.gz")
    before = source.read_bytes()

    with pytest.raises(VerificationError, match="negative source epoch"):
        canonicalize_sdist(source, _resolve_epoch(-1))
    assert source.read_bytes() == before


def test_source_epoch_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    assert _resolve_epoch(None) == EPOCH
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "  ")
    with pytest.raises(VerificationError, match="no source epoch"):
        _resolve_epoch(None)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "not-a-number")
    with pytest.raises(VerificationError, match="invalid SOURCE_DATE_EPOCH"):
        _resolve_epoch(None)
