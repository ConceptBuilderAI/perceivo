"""Pin and verify the public orchestration packages against their published archives.

Each package under ``examples/orchestrations/`` is the unmodified contents of a
downloadable ``<slug>.zip`` whose only top-level entry is the ``<slug>/``
folder. The packages carry no hash index of their own, and one cannot be added
inside them without making them differ from the archive, so the pins live
beside them in ``examples/PACKAGE_HASHES.json``.

The pins are written only from the archives themselves (``--write --archive``),
never from the tree, so a byte changed in the tree cannot be blessed by
refreshing. Verification then requires every package folder to hold exactly
its pinned files with exactly the pinned bytes, and every pinned package to be
present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

PACKAGE_ROOT = "examples/orchestrations"
PINS = "examples/PACKAGE_HASHES.json"


class VerificationError(RuntimeError):
    """A checked identity does not describe the selected public bytes."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


class Source:
    """Read exact bytes from the worktree, index, or an immutable Git tree."""

    def __init__(self, root: Path, selector: str):
        self.root = root.resolve()
        self.selector = selector

    def _git(self, *args: str) -> bytes:
        completed = subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=False)
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise VerificationError(f"git {' '.join(args[:3])} failed: {detail}")
        return completed.stdout

    def read(self, relative: str) -> bytes:
        if self.selector == "worktree":
            return self.root.joinpath(*PurePosixPath(relative).parts).read_bytes()
        spec = f":{relative}" if self.selector == "index" else f"{self.selector}:{relative}"
        return self._git("show", spec)

    def paths(self, prefix: str) -> list[str]:
        if self.selector == "worktree":
            # Every file on disk counts, tracked or not: a stray file inside a
            # package folder makes it differ from its archive either way.
            base = self.root.joinpath(*PurePosixPath(prefix).parts)
            return sorted(
                path.relative_to(self.root).as_posix() for path in base.rglob("*") if path.is_file()
            )
        if self.selector == "index":
            output = self._git("ls-files", "--", prefix)
        else:
            output = self._git("ls-tree", "-r", "--name-only", self.selector, "--", prefix)
        return sorted(line for line in output.decode().splitlines() if line)


def archive_pins(archive: Path) -> tuple[str, dict[str, object]]:
    """Read one package archive into its slug and pin record."""
    data = archive.read_bytes()
    slug = archive.name.removesuffix(".zip")
    if slug == archive.name or not slug:
        raise VerificationError(f"{archive.name}: archive name must be <slug>.zip")
    files: dict[str, str] = {}
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            parts = PurePosixPath(info.filename).parts
            if "\\" in info.filename or not parts or parts[0] != slug or ".." in parts:
                raise VerificationError(f"{archive.name}: entry outside {slug}/: {info.filename}")
            if info.is_dir():
                continue
            relative = PurePosixPath(*parts[1:]).as_posix()
            if relative in files:
                raise VerificationError(f"{archive.name}: duplicate entry {info.filename}")
            files[relative] = sha256(bundle.read(info))
    if not files:
        raise VerificationError(f"{archive.name}: archive holds no files")
    return slug, {"archive": archive.name, "archive_sha256": sha256(data), "files": files}


def pins_from_archives(archives: list[Path]) -> dict[str, object]:
    packages: dict[str, object] = {}
    for archive in archives:
        slug, record = archive_pins(archive)
        if slug in packages:
            raise VerificationError(f"two archives for package {slug}")
        packages[slug] = record
    return {"algorithm": "sha256", "packages": packages}


def verify(source: Source, archives: list[Path]) -> tuple[int, int]:
    """Check the selected bytes against the pins; return (packages, files) checked."""
    attributes = source.read(".gitattributes")
    if attributes != b"* text=auto eol=lf\n":
        raise VerificationError(".gitattributes must enforce LF for all text files")
    pins = json.loads(source.read(PINS))
    packages: dict[str, dict] = pins["packages"]
    if not packages:
        raise VerificationError(f"{PINS} pins no packages")
    for archive in archives:
        slug, record = archive_pins(archive)
        if packages.get(slug) != record:
            raise VerificationError(f"{archive.name} does not match its pins in {PINS}")

    found: dict[str, list[str]] = {}
    for path in source.paths(PACKAGE_ROOT):
        parts = PurePosixPath(path).parts
        if len(parts) < 4:
            raise VerificationError(f"file outside any package folder: {path}")
        found.setdefault(parts[2], []).append(path)
    unpinned = sorted(set(found) - set(packages))
    if unpinned:
        raise VerificationError("package folder(s) with no pins: " + ", ".join(unpinned))
    absent = sorted(set(packages) - set(found))
    if absent:
        raise VerificationError("pinned package(s) absent: " + ", ".join(absent))

    failures: list[str] = []
    for slug, record in sorted(packages.items()):
        prefix = f"{PACKAGE_ROOT}/{slug}/"
        actual = {path.removeprefix(prefix) for path in found[slug]}
        expected: dict[str, str] = record["files"]
        failures += [f"unpinned file {prefix}{name}" for name in sorted(actual - set(expected))]
        failures += [f"missing file {prefix}{name}" for name in sorted(set(expected) - actual)]
        failures += [
            f"{prefix}{name}"
            for name in sorted(actual & set(expected))
            if sha256(source.read(prefix + name)) != expected[name]
        ]
    if failures:
        raise VerificationError(
            "package bytes differ from their archive pins: " + ", ".join(failures)
        )
    return len(packages), sum(len(record["files"]) for record in packages.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source", default="index", help="index, worktree, or a Git treeish")
    parser.add_argument(
        "--archive",
        type=Path,
        action="append",
        default=[],
        help="a published <slug>.zip; with --write, the only input the pins are taken from",
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        if not args.archive:
            parser.error("--write takes its pins only from --archive files")
        pins = pins_from_archives(args.archive)
        args.root.joinpath(*PurePosixPath(PINS).parts).write_bytes(json_bytes(pins))
        print(f"pinned {len(pins['packages'])} example packages from their archives")
        return 0
    count, files = verify(Source(args.root, args.source), args.archive)
    print(f"verified {count} example packages ({files} files) from {args.source}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        raise SystemExit(str(exc)) from exc
