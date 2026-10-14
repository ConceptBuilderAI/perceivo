#!/usr/bin/env python3
"""Prove that distribution bytes are exactly the bytes the build job produced.

``digests`` checks the build job's two distributions against its SHA256SUMS
            and prints ``wheel_name=``, ``wheel_sha256=``, ``sdist_name=`` and
            ``sdist_sha256=`` lines, and nothing else, for ``$GITHUB_OUTPUT``.
            Those job outputs are the only digests later jobs trust: a job
            output is fixed when the build job ends, while a run artifact can
            be replaced by any later job in the run.

``sums``    checks a directory of distributions against a ``SHA256SUMS`` file.

``index``   downloads the files a package index serves for one release and
            checks them against ``--expect NAME=SHA256`` (the build job's
            outputs): the same two file names, and every sha256 equal, both as
            the index reports it and as the downloaded bytes hash. The files
            are kept, so the smoke that follows installs the index's bytes.

Every match prints ``sha256 match <name> <sha256>``. Every path must resolve
inside the working directory; nothing on the command line can widen that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

WHEEL_SUFFIX: Final = ".whl"
SDIST_SUFFIX: Final = ".tar.gz"
SHA256: Final = re.compile(r"[0-9a-f]{64}")
DISTRIBUTION: Final = re.compile(r"[A-Za-z0-9_.+-]+(?:\.whl|\.tar\.gz)")
ALLOWED_INDEXES: Final = {
    "https://pypi.org": "files.pythonhosted.org",
    "https://test.pypi.org": "test-files.pythonhosted.org",
}

Fetch = Callable[[str], bytes]


class Mismatch(Exception):
    """The bytes are not the canonical build's bytes."""


def confine(path: Path, root: Path) -> Path:
    """Resolve ``path`` against ``root``; refuse anything that lands outside it.

    Symbolic links, ``..`` segments and absolute paths are all resolved first,
    so the check applies to the file that would actually be opened.
    """
    base = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(base, path))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise Mismatch(f"path {str(path)!r} is outside the allowed root {base!r}")
    return Path(candidate)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def local_distributions(directory: Path) -> dict[str, str]:
    """Map each distribution file in ``directory`` to its sha256."""
    if not directory.is_dir():
        raise Mismatch(f"{directory} is not a directory")
    files = {path.name: _sha(path.read_bytes()) for path in directory.iterdir() if path.is_file()}
    unexpected = sorted(name for name in files if not DISTRIBUTION.fullmatch(name))
    if unexpected:
        raise Mismatch(f"extra non-distribution files in {directory}: {unexpected}")
    wheels = [name for name in files if name.endswith(WHEEL_SUFFIX)]
    sdists = [name for name in files if name.endswith(SDIST_SUFFIX)]
    if len(wheels) != 1 or len(sdists) != 1:
        raise Mismatch(
            f"expected exactly one wheel and one sdist in {directory}, found {sorted(files)}"
        )
    return files


def parse_sums(text: str) -> dict[str, str]:
    """Parse ``sha256sum`` output: ``<64-hex>  <name>`` per line."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^/\\]+)", line.strip())
        if not match or match.group(2) in sums:
            raise Mismatch(f"malformed or duplicate SHA256SUMS line: {line!r}")
        sums[match.group(2)] = match.group(1)
    if not sums:
        raise Mismatch("SHA256SUMS is empty")
    return sums


def _compare(expected: dict[str, str], actual: dict[str, str], label: str) -> list[str]:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        raise Mismatch(f"{label}: file names differ: missing={missing}, extra={extra}")
    lines = []
    for name in sorted(expected):
        if expected[name] != actual[name]:
            raise Mismatch(
                f"{label}: sha256 differs for {name}: {expected[name]} != {actual[name]}"
            )
        lines.append(f"sha256 match {name} {expected[name]}")
    return lines


def check_sums(directory: Path, sums_file: Path, root: Path = Path(".")) -> list[str]:
    expected = parse_sums(confine(sums_file, root).read_text(encoding="utf-8"))
    local = local_distributions(confine(directory, root))
    return _compare(expected, local, "local vs SHA256SUMS")


def _one_wheel_one_sdist(files: dict[str, str], label: str) -> None:
    wheels = [name for name in files if name.endswith(WHEEL_SUFFIX)]
    sdists = [name for name in files if name.endswith(SDIST_SUFFIX)]
    if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise Mismatch(f"{label}: expected exactly one wheel and one sdist, found {sorted(files)}")


def parse_expected(values: list[str]) -> dict[str, str]:
    """Parse ``NAME=SHA256`` pairs: exactly one wheel and one sdist."""
    expected: dict[str, str] = {}
    for value in values:
        name, separator, digest = value.partition("=")
        if not separator or not DISTRIBUTION.fullmatch(name) or not SHA256.fullmatch(digest):
            raise Mismatch(f"--expect must be NAME=SHA256, got {value!r}")
        if name in expected:
            raise Mismatch(f"--expect names {name} twice")
        expected[name] = digest
    _one_wheel_one_sdist(expected, "--expect")
    return expected


def digests(directory: Path, sums_file: Path, root: Path = Path(".")) -> list[str]:
    """The build job's output lines, after proving the files match SHA256SUMS."""
    check_sums(directory, sums_file, root)
    files = parse_sums(confine(sums_file, root).read_text(encoding="utf-8"))
    _one_wheel_one_sdist(files, "SHA256SUMS")
    wheel = next(name for name in files if name.endswith(WHEEL_SUFFIX))
    sdist = next(name for name in files if name.endswith(SDIST_SUFFIX))
    return [
        f"wheel_name={wheel}",
        f"wheel_sha256={files[wheel]}",
        f"sdist_name={sdist}",
        f"sdist_sha256={files[sdist]}",
    ]


def _default_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "perceivo-release-check"})
    if urlsplit(url).scheme != "https":
        raise Mismatch(f"refusing a non-https URL: {url!r}")
    with urllib.request.urlopen(request, timeout=60) as response:
        data: bytes = response.read()
    return data


def _release_files(
    index_url: str, project: str, version: str, fetch: Fetch
) -> list[dict[str, object]]:
    payload = json.loads(fetch(f"{index_url}/pypi/{project}/{version}/json"))
    urls = payload.get("urls") if isinstance(payload, dict) else None
    if not isinstance(urls, list):
        raise Mismatch("index JSON has no urls list")
    return [item for item in urls if isinstance(item, dict)]


def check_index(
    expected: dict[str, str],
    index_url: str,
    project: str,
    version: str,
    download_directory: Path,
    fetch: Fetch = _default_fetch,
    root: Path = Path("."),
) -> list[str]:
    """Download one release from an index and compare it with the expected digests."""
    index_url = index_url.rstrip("/")
    files_host = ALLOWED_INDEXES.get(index_url)
    if files_host is None:
        raise Mismatch(f"index {index_url!r} is not an allowed index")
    _one_wheel_one_sdist(expected, "expected digests")
    download_directory = confine(download_directory, root)
    download_directory.mkdir(parents=True, exist_ok=True)
    served: dict[str, str] = {}
    for item in _release_files(index_url, project, version, fetch):
        name, url = str(item.get("filename", "")), str(item.get("url", ""))
        digests = item.get("digests")
        reported = str(digests.get("sha256", "")) if isinstance(digests, dict) else ""
        if not DISTRIBUTION.fullmatch(name) or name in served:
            raise Mismatch(f"index served an unsafe or duplicate file name: {name!r}")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != files_host:
            raise Mismatch(f"index served {name} from an unexpected location: {url!r}")
        if not SHA256.fullmatch(reported):
            raise Mismatch(f"index reports no sha256 for {name}")
        data = fetch(url)
        if _sha(data) != reported:
            raise Mismatch(f"downloaded bytes of {name} do not hash to the index's sha256")
        (download_directory / name).write_bytes(data)
        served[name] = reported
    return _compare(expected, served, f"build outputs vs {index_url}")


def _retrying(action: Callable[[], list[str]], attempts: int, delay: float) -> list[str]:
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except urllib.error.HTTPError as exc:
            # A new release can take a little while to appear in the JSON API.
            if exc.code != 404 or attempt == attempts:
                raise
            print(
                f"release not yet served (attempt {attempt}/{attempts}); waiting", file=sys.stderr
            )
            time.sleep(delay)
    raise Mismatch("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    digest_parser = subparsers.add_parser("digests")
    digest_parser.add_argument("--dist", type=Path, required=True)
    digest_parser.add_argument("--sums", type=Path, required=True)
    sums = subparsers.add_parser("sums")
    sums.add_argument("--dist", type=Path, required=True)
    sums.add_argument("--sums", type=Path, required=True)
    index = subparsers.add_parser("index")
    index.add_argument(
        "--expect",
        action="append",
        required=True,
        help="NAME=SHA256 from the build job's outputs; once for the wheel, once for the sdist",
    )
    index.add_argument("--index-url", required=True, choices=sorted(ALLOWED_INDEXES))
    index.add_argument("--project", default="perceivo")
    index.add_argument("--version", required=True)
    index.add_argument("--download-dir", type=Path, required=True)
    index.add_argument("--attempts", type=int, default=10)
    index.add_argument("--delay", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "digests":
            # stdout is appended to $GITHUB_OUTPUT: key=value lines only.
            for line in digests(args.dist, args.sums, Path.cwd()):
                print(line)
            print("build digests recorded", file=sys.stderr)
            return 0
        if args.command == "sums":
            lines = check_sums(args.dist, args.sums, Path.cwd())
        else:
            lines = _retrying(
                lambda: check_index(
                    parse_expected(args.expect),
                    args.index_url,
                    args.project,
                    args.version,
                    args.download_dir,
                    root=Path.cwd(),
                ),
                args.attempts,
                args.delay,
            )
    except (Mismatch, OSError, ValueError) as exc:  # URLError is an OSError
        print(f"artifact comparison failed: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print(f"artifact comparison passed: {len(lines)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
