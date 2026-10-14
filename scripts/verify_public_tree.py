#!/usr/bin/env python3
"""Seal and verify the closed public Perceivo source and artifact boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import Final, Iterable, Mapping, NotRequired, TypedDict, cast

canonical_archives = import_module(
    "scripts.canonical_archives" if __package__ else "canonical_archives"
)
VerificationError = canonical_archives.VerificationError
canonicalize_sdist = canonical_archives.canonicalize_sdist
canonicalize_wheel = canonical_archives.canonicalize_wheel
_resolve_epoch = canonical_archives.resolve_epoch

MANIFEST_NAME: Final = "SOURCE-PROVENANCE.json"
REGULAR_MODES: Final = {"100644", "100755"}
DENIED_PARTS: Final = {
    ".claude",
    ".dri",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    "__pycache__",
    "migration",
    "perceivo-client",
    "perceivo-platform",
    "perceivo_platform",
}
# Validator rule codes the shipped SDK emits: the ``add(...)`` prefixes in
# perceivo/validator/_component.py, _declaration_truth.py,
# _response_contract.py, _invoke_reads.py and _absent_manifest.py, and the
# bracketed codes in perceivo/validator/_orchestration/_node_rules.py. A
# ``VAL-...`` token outside these and ``PLATFORM_DOCUMENTED_VAL_CODES`` is an
# internal rule id leaking into public text.
SDK_EMITTED_VAL_CODES: Final = (
    "ASYNC",
    "BASE",
    "ENTRY",
    "IMPORT",
    "METHOD",
    "RECORD",
    "SCRIPT",
    "SYNTAX",
    "TYPE",
    "CONTRACT-DYNAMIC",
    "CONTRACT-OPTIONAL",
    "CONTRACT-PASSTHROUGH",
    "CONTRACT-RESP",
    "UNVERIFIABLE-MANIFEST",
    "UNVERIFIABLE-RESP",
    "PARAM-001",
    "SECRET-001",
)
# A platform rule code the SDK documents in perceivo/_bundle (the edge the
# bundle writer translates) and does not emit itself.
PLATFORM_DOCUMENTED_VAL_CODES: Final = ("MAINFLOW-OUT-001",)


@dataclass(frozen=True)
class IndexEntry:
    path: str
    mode: str
    object_id: str


class FileRecord(TypedDict):
    path: str
    mode: str
    sha256: str
    origin: str
    object_id: str
    transformation: str


class ProvenanceManifest(TypedDict):
    schema_version: int
    exporter: dict[str, str]
    inputs: dict[str, str]
    selectors: dict[str, object]
    transformations: list[str]
    files: list[FileRecord]
    tree_digest: str
    normalization: NotRequired[str]
    manifest_exclusion: NotRequired[str]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:500]
        raise VerificationError(f"git {' '.join(args[:4])} failed: {detail}")
    return completed.stdout


def _path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or "\\" in path:
        raise VerificationError(f"unsafe path: {path!r}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise VerificationError(f"unsafe path segments: {path!r}")
    return candidate


def _index(root: Path) -> dict[str, IndexEntry]:
    raw = _git(root, "ls-files", "--stage", "-z")
    entries: dict[str, IndexEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        path = encoded_path.decode("utf-8", "strict")
        _path(path)
        if stage != "0" or path in entries:
            raise VerificationError(f"unmerged or duplicate index entry: {path}")
        if mode not in REGULAR_MODES:
            raise VerificationError(f"unsupported Git mode {mode} for {path}")
        entries[path] = IndexEntry(path, mode, object_id)
    return entries


#: The public orchestration packages this repository authors. The
#: internal-vocabulary rules below apply HERE and only here: everything under
#: ``perceivo/`` and ``tests/unit/`` is vendored byte-for-byte from an SDK
#: release by ``export_public_repository.py``, so a word this repository
#: dislikes in that text is an intake item for the SDK, not something a
#: packages change may silently rewrite. Rewriting it here would also make the
#: vendored tree differ from the release it claims to be, which is the property
#: ``SOURCE-PROVENANCE.json`` exists to record.
PACKAGE_PREFIX: Final = "examples/orchestrations/"


def _denied_patterns(path: str | None = None) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """The rules that apply to ``path``.

    ``path`` of ``None`` returns every rule, for a caller that wants the whole
    catalogue rather than a decision about one file.
    """
    if path is None:
        return _universal_patterns() + _package_patterns()
    if path.startswith(PACKAGE_PREFIX):
        return _universal_patterns() + _package_patterns()
    return _universal_patterns()


def _universal_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Rules that hold everywhere in the tree, vendored SDK included."""
    ticket = "TI" + "CK"
    claimed = "claim" + "ed"
    private_repo = "_perceivo" + "-sdk"
    return (
        (
            "private ticket identifier",
            re.compile(rf"(?<![^\W_]){ticket}(?:ET)?[-_ #]{{0,2}}\d+", re.IGNORECASE),
        ),
        (
            "private claim marker",
            re.compile(rf"\b{claimed}:[a-z0-9_-]+\b", re.IGNORECASE),
        ),
        (
            "private source repository URL",
            re.compile(rf"github\.com[/ :]ConceptBuilderAI[/ ]{private_repo}", re.IGNORECASE),
        ),
        ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
        ("storage account key", re.compile(r"\bAccountKey=[A-Za-z0-9+/=]{20,}")),
        ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    )


def _package_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Internal-vocabulary rules, scoped to ``PACKAGE_PREFIX``."""
    substrate = "r" + "ay"
    docs_repo = "perceivo" + "-documentation"
    agent_pipeline = "Agent" + " " + "Pipeline"
    codename = "lex" + "ograph"
    graph_classifier = "execution-graph" + "-classifier"
    bench = "be" + "nch"
    # A family is emitted as ``<family>-NNN`` and may be named bare; a code
    # that already ends in ``-NNN`` is allowed only as itself. Nothing may
    # follow either, so a longer token in an allowed family is still refused.
    val_allowed = SDK_EMITTED_VAL_CODES + PLATFORM_DOCUMENTED_VAL_CODES
    numbered = re.compile(r"-\d{3}$")
    val_families = "|".join(re.escape(code) for code in val_allowed if not numbered.search(code))
    val_codes = "|".join(re.escape(code) for code in val_allowed if numbered.search(code))
    val_allowed_pattern = rf"(?:(?:{val_families})(?:-\d{{3}})?|(?:{val_codes}))(?![-\w])"
    return (
        ("execution substrate name", re.compile(rf"\b{substrate}\b", re.IGNORECASE)),
        (
            "internal pitfalls-catalogue reference",
            re.compile(r"\bTrap\s+\d+\b", re.IGNORECASE),
        ),
        (
            "unshipped validator rule code",
            re.compile(rf"\bVAL-(?!{val_allowed_pattern})[A-Z]+(?:-[A-Z]+)*(?:-\d{{3}})?\b"),
        ),
        ("internal documentation repository name", re.compile(rf"\b{docs_repo}\b", re.IGNORECASE)),
        (
            "internal process document reference",
            re.compile(rf"\b{agent_pipeline}\b", re.IGNORECASE),
        ),
        # Letter boundaries rather than ``\b``: ``\b`` treats ``_`` as a word
        # character, so a ``<codename>_corpus`` path would slip past it.
        (
            "internal codename",
            re.compile(rf"(?<![a-z]){codename}(?![a-z])", re.IGNORECASE),
        ),
        ("internal component reference", re.compile(rf"\b{graph_classifier}\b", re.IGNORECASE)),
        ("internal bench/trap reference", re.compile(rf"\b{bench}\b", re.IGNORECASE)),
    )


def _scan(path: str, data: bytes) -> None:
    candidate = _path(path)
    parts = {part.casefold() for part in candidate.parts}
    if parts.intersection(DENIED_PARTS) or candidate.name.casefold().startswith(".env"):
        raise VerificationError(f"denied path: {path}")
    if candidate.suffix.casefold() in {".pyc", ".pyo"}:
        raise VerificationError(f"denied cache artifact: {path}")
    if b"\0" in data:
        return
    text = data.decode("utf-8", "strict")
    for label, pattern in _denied_patterns(path):
        if pattern.search(text):
            raise VerificationError(f"{label} found in {path}")


def _tree_digest(files: Iterable[Mapping[str, object]]) -> str:
    rows = "".join(
        f"{item['path']}\0{item['mode']}\0{item['sha256']}\n"
        for item in sorted(files, key=lambda value: str(value["path"]))
    )
    return _sha(rows.encode("utf-8"))


def _load_manifest(path: Path) -> ProvenanceManifest:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read provenance manifest: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise VerificationError("unsupported provenance schema")
    inputs = value.get("inputs")
    if not isinstance(inputs, dict):
        raise VerificationError("provenance inputs are absent")
    # Commit pins are optional: a published tree may omit them when the
    # commits they name are not public. When present they must be full SHAs.
    for field in ("source_commit", "destination_commit"):
        if field in inputs and not re.fullmatch(r"[0-9a-f]{40}", str(inputs[field])):
            raise VerificationError(f"provenance {field} is not a full SHA-1")
    return cast(ProvenanceManifest, value)


def _manifest_files(manifest: ProvenanceManifest) -> dict[str, FileRecord]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise VerificationError("provenance files must be a list")
    files: dict[str, FileRecord] = {}
    required = {"path", "mode", "sha256", "origin", "object_id", "transformation"}
    for value in raw_files:
        if not isinstance(value, dict) or not required.issubset(value):
            raise VerificationError("invalid provenance file entry")
        path = str(value["path"])
        _path(path)
        if path == MANIFEST_NAME or path in files:
            raise VerificationError(f"duplicate or self-referential provenance path: {path}")
        if value["mode"] not in REGULAR_MODES:
            raise VerificationError(f"invalid provenance mode for {path}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(value["sha256"])):
            raise VerificationError(f"invalid SHA-256 for {path}")
        if not re.fullmatch(r"[0-9a-f]{40}", str(value["object_id"])):
            raise VerificationError(f"invalid Git object ID for {path}")
        files[path] = cast(FileRecord, value)
    return files


def seal_repository(root: Path, seed: Path, output: Path) -> ProvenanceManifest:
    """Seal a staged final tree while retaining source provenance when bytes match."""
    root = root.resolve(strict=True)
    seed_manifest = _load_manifest(seed)
    seed_files = _manifest_files(seed_manifest)
    index = _index(root)
    if MANIFEST_NAME in index:
        index.pop(MANIFEST_NAME)
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all").decode(
        "utf-8", "strict"
    )
    untracked = [line[3:] for line in status.splitlines() if line.startswith("?? ")]
    if untracked:
        raise VerificationError(f"untracked files must be staged before sealing: {untracked}")

    files: list[FileRecord] = []
    for path in sorted(index):
        entry = index[path]
        data = root.joinpath(*_path(path).parts).read_bytes()
        object_data = _git(root, "cat-file", "blob", entry.object_id)
        if data != object_data:
            raise VerificationError(f"working bytes differ from staged bytes: {path}")
        _scan(path, data)
        old = seed_files.get(path)
        if old is not None and old["sha256"] == _sha(data) and old["mode"] == entry.mode:
            origin = old["origin"]
            source_object = old["object_id"]
            transformation = old["transformation"]
        else:
            origin = "public-destination"
            source_object = entry.object_id
            transformation = "public-destination-overlay"
        files.append(
            {
                "path": path,
                "mode": entry.mode,
                "sha256": _sha(data),
                "origin": origin,
                "object_id": source_object,
                "transformation": transformation,
            }
        )

    manifest = cast(ProvenanceManifest, dict(seed_manifest))
    transformations = list(manifest["transformations"])
    for item in ("public-destination-overlay", "provenance-seal-v1"):
        if item not in transformations:
            transformations.append(item)
    manifest.update(
        {
            "transformations": transformations,
            "manifest_exclusion": "SOURCE-PROVENANCE.json is excluded to avoid a recursive self-hash",
            "files": files,
            "tree_digest": _tree_digest(files),
        }
    )
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_repository(root: Path, manifest_path: Path | None = None) -> ProvenanceManifest:
    root = root.resolve(strict=True)
    path = manifest_path or root / MANIFEST_NAME
    manifest = _load_manifest(path)
    files = _manifest_files(manifest)
    index = _index(root)
    if MANIFEST_NAME not in index:
        raise VerificationError(f"{MANIFEST_NAME} is not tracked")
    index.pop(MANIFEST_NAME)
    if set(index) != set(files):
        missing = sorted(set(index).difference(files))
        absent = sorted(set(files).difference(index))
        raise VerificationError(f"manifest/tree mismatch: unmanifested={missing}, absent={absent}")
    for name, expected in files.items():
        entry = index[name]
        data = root.joinpath(*_path(name).parts).read_bytes()
        if entry.mode != expected["mode"] or _sha(data) != expected["sha256"]:
            raise VerificationError(f"mode or content hash mismatch: {name}")
        if data != _git(root, "cat-file", "blob", entry.object_id):
            raise VerificationError(f"working bytes differ from tracked object: {name}")
        _scan(name, data)
    if manifest.get("tree_digest") != _tree_digest(files.values()):
        raise VerificationError("tree digest mismatch")
    top_packages = {
        name.split("/", 1)[0]
        for name in files
        if name.casefold().startswith("perceivo") and "/" in name
    }
    if top_packages != {"perceivo"}:
        raise VerificationError(f"invalid package boundary: {sorted(top_packages)}")
    if "perceivo/py.typed" not in files:
        raise VerificationError("typed-package marker is absent")
    return manifest


def _archive_members(path: Path) -> dict[str, str]:
    members: dict[str, str] = {}
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for zip_info in archive.infolist():
                if zip_info.is_dir():
                    continue
                name = _path(zip_info.filename).as_posix()
                if name in members:
                    raise VerificationError(f"duplicate archive member in {path.name}: {name}")
                data = archive.read(zip_info)
                _scan(name, data)
                members[name] = _sha(data)
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for tar_info in archive.getmembers():
                if tar_info.isdir():
                    continue
                if not tar_info.isfile():
                    raise VerificationError(
                        f"non-regular archive member in {path.name}: {tar_info.name}"
                    )
                parts = _path(tar_info.name).parts
                if len(parts) < 2:
                    raise VerificationError(f"sdist member lacks root prefix: {tar_info.name}")
                name = PurePosixPath(*parts[1:]).as_posix()
                extracted = archive.extractfile(tar_info)
                if extracted is None:
                    raise VerificationError(f"cannot read archive member: {tar_info.name}")
                if name in members:
                    raise VerificationError(f"duplicate archive member in {path.name}: {name}")
                data = extracted.read()
                _scan(name, data)
                members[name] = _sha(data)
    else:
        raise VerificationError(f"unsupported archive: {path}")
    return members


def verify_archives(root: Path, wheel: Path, sdist: Path) -> None:
    manifest = verify_repository(root)
    files = _manifest_files(manifest)
    wheel_members = _archive_members(wheel)
    sdist_members = _archive_members(sdist)
    required_package = {
        name: value["sha256"] for name, value in files.items() if name.startswith("perceivo/")
    }
    for name, digest in required_package.items():
        if wheel_members.get(name) != digest or sdist_members.get(name) != digest:
            raise VerificationError(f"package member missing or changed in archives: {name}")
    for members, label in ((wheel_members, "wheel"), (sdist_members, "sdist")):
        names = set(members)
        if "perceivo/py.typed" not in names:
            raise VerificationError(f"py.typed absent from {label}")
        for legal in ("LICENSE", "NOTICE"):
            if not any(name == legal or name.endswith(f"/{legal}") for name in names):
                raise VerificationError(f"{legal} absent from {label}")
        if any(set(_path(name).parts).intersection(DENIED_PARTS) for name in names):
            raise VerificationError(f"private package path present in {label}")


def compare_archive_sets(first: Path, second: Path) -> None:
    def collect(directory: Path) -> dict[str, dict[str, str]]:
        archives = sorted([*directory.glob("*.whl"), *directory.glob("*.tar.gz")])
        return {path.name: _archive_members(path) for path in archives}

    left, right = collect(first), collect(second)
    if not left or left != right:
        raise VerificationError("archive member paths or hashes are not reproducible")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, default=Path("."))
    seal = subparsers.add_parser("seal")
    seal.add_argument("--root", type=Path, default=Path("."))
    seal.add_argument("--seed", type=Path, required=True)
    seal.add_argument("--output", type=Path, default=Path(MANIFEST_NAME))
    archives = subparsers.add_parser("archives")
    archives.add_argument("--root", type=Path, default=Path("."))
    archives.add_argument("--wheel", type=Path, required=True)
    archives.add_argument("--sdist", type=Path, required=True)
    compare = subparsers.add_parser("compare-archives")
    compare.add_argument("--first", type=Path, required=True)
    compare.add_argument("--second", type=Path, required=True)
    for command in ("canonicalize-sdist", "canonicalize-wheel"):
        canonicalize = subparsers.add_parser(command)
        canonicalize.add_argument("--source", type=Path, required=True)
        canonicalize.add_argument("--output", type=Path, default=None)
        canonicalize.add_argument(
            "--epoch",
            type=int,
            default=None,
            help="source epoch in seconds; defaults to SOURCE_DATE_EPOCH",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify":
            verify_repository(args.root)
        elif args.command == "seal":
            seal_repository(args.root, args.seed, args.output)
        elif args.command == "archives":
            verify_archives(args.root, args.wheel, args.sdist)
        elif args.command in {"canonicalize-sdist", "canonicalize-wheel"}:
            rewrite = (
                canonicalize_sdist if args.command == "canonicalize-sdist" else canonicalize_wheel
            )
            destination = rewrite(args.source, _resolve_epoch(args.epoch), args.output)
            print(f"canonical {destination.name} sha256={_sha(destination.read_bytes())}")
        else:
            compare_archive_sets(args.first, args.second)
    except (VerificationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"public tree rejected: {exc}", file=sys.stderr)
        return 1
    print(f"public {args.command} verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
