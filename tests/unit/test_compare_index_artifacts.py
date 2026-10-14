"""Byte-equality between the build job's outputs, its files, SHA256SUMS and an index."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts import compare_index_artifacts as compare
from scripts.compare_index_artifacts import (
    Mismatch,
    check_index,
    check_sums,
    digests,
    main,
    parse_expected,
)


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # CLI paths are confined to the working directory.
    monkeypatch.chdir(tmp_path)


WHEEL = "perceivo-1.0.0rc1-py3-none-any.whl"
SDIST = "perceivo-1.0.0rc1.tar.gz"
FILES = "https://test-files.pythonhosted.org/packages/ab/cd"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: What the build job's outputs say (the only trusted digests).
EXPECTED = {WHEEL: _sha_of(b"wheel-bytes"), SDIST: _sha_of(b"sdist-bytes")}


def _dist(root: Path) -> Path:
    dist = root / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(b"wheel-bytes")
    (dist / SDIST).write_bytes(b"sdist-bytes")
    return dist


def _sums(root: Path, dist: Path) -> Path:
    path = root / "SHA256SUMS"
    lines = [f"{_sha(p.read_bytes())}  {p.name}" for p in sorted(dist.iterdir())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class FakeIndex:
    def __init__(self, files: dict[str, bytes], reported: dict[str, str] | None = None) -> None:
        self.files = files
        self.reported = reported or {}
        self.requested: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.requested.append(url)
        if url.endswith("/json"):
            urls = [
                {
                    "filename": name,
                    "url": f"{FILES}/{name}",
                    "digests": {"sha256": self.reported.get(name, _sha(data))},
                }
                for name, data in self.files.items()
            ]
            return json.dumps({"urls": urls}).encode()
        return self.files[url.rsplit("/", 1)[1]]


def test_sums_match(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dist = _dist(tmp_path)
    sums = _sums(tmp_path, dist)
    assert main(["sums", "--dist", str(dist), "--sums", str(sums)]) == 0
    out = capsys.readouterr().out
    assert f"sha256 match {WHEEL}" in out
    assert f"sha256 match {SDIST}" in out


def test_sums_flipped_byte_fails(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    sums = _sums(tmp_path, dist)
    (dist / WHEEL).write_bytes(b"wheel-bytez")
    with pytest.raises(Mismatch, match="sha256 differs"):
        check_sums(dist, sums, tmp_path)


def test_sums_missing_sdist_fails(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    sums = _sums(tmp_path, dist)
    (dist / SDIST).unlink()
    with pytest.raises(Mismatch):
        check_sums(dist, sums, tmp_path)


def test_sums_extra_file_fails(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    sums = _sums(tmp_path, dist)
    (dist / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(Mismatch, match="extra non-distribution files"):
        check_sums(dist, sums, tmp_path)


def test_sums_extra_wheel_fails(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    (dist / "perceivo-1.0.0rc1-py311-none-any.whl").write_bytes(b"other")
    sums = _sums(tmp_path, dist)
    with pytest.raises(Mismatch, match="exactly one wheel"):
        check_sums(dist, sums, tmp_path)


def test_index_match_keeps_served_bytes(tmp_path: Path) -> None:
    fetch = FakeIndex({WHEEL: b"wheel-bytes", SDIST: b"sdist-bytes"})
    served = tmp_path / "served"
    lines = check_index(
        EXPECTED, "https://test.pypi.org", "perceivo", "1.0.0rc1", served, fetch, root=tmp_path
    )
    assert len(lines) == 2
    assert (served / WHEEL).read_bytes() == b"wheel-bytes"
    assert fetch.requested[0] == "https://test.pypi.org/pypi/perceivo/1.0.0rc1/json"


def test_index_flipped_byte_fails(tmp_path: Path) -> None:
    fetch = FakeIndex({WHEEL: b"wheel-bytez", SDIST: b"sdist-bytes"})
    with pytest.raises(Mismatch, match="sha256 differs"):
        check_index(
            EXPECTED,
            "https://test.pypi.org",
            "perceivo",
            "1.0.0rc1",
            tmp_path / "s",
            fetch,
            root=tmp_path,
        )


def test_index_missing_sdist_fails(tmp_path: Path) -> None:
    fetch = FakeIndex({WHEEL: b"wheel-bytes"})
    with pytest.raises(Mismatch, match="missing"):
        check_index(
            EXPECTED,
            "https://test.pypi.org",
            "perceivo",
            "1.0.0rc1",
            tmp_path / "s",
            fetch,
            root=tmp_path,
        )


def test_index_extra_file_fails(tmp_path: Path) -> None:
    fetch = FakeIndex(
        {WHEEL: b"wheel-bytes", SDIST: b"sdist-bytes", "perceivo-1.0.0rc1-py3-none-win32.whl": b"x"}
    )
    with pytest.raises(Mismatch, match="extra"):
        check_index(
            EXPECTED,
            "https://test.pypi.org",
            "perceivo",
            "1.0.0rc1",
            tmp_path / "s",
            fetch,
            root=tmp_path,
        )


def test_index_reported_digest_disagreeing_with_bytes_fails(tmp_path: Path) -> None:
    fetch = FakeIndex(
        {WHEEL: b"wheel-bytes", SDIST: b"sdist-bytes"}, reported={WHEEL: _sha(b"other")}
    )
    with pytest.raises(Mismatch, match="do not hash"):
        check_index(
            EXPECTED,
            "https://test.pypi.org",
            "perceivo",
            "1.0.0rc1",
            tmp_path / "s",
            fetch,
            root=tmp_path,
        )


def test_index_foreign_file_host_fails(tmp_path: Path) -> None:
    fetch = FakeIndex({WHEEL: b"wheel-bytes", SDIST: b"sdist-bytes"})
    # pypi.org serves from files.pythonhosted.org, not the TestPyPI file host.
    with pytest.raises(Mismatch, match="unexpected location"):
        check_index(
            EXPECTED,
            "https://pypi.org",
            "perceivo",
            "1.0.0rc1",
            tmp_path / "s",
            fetch,
            root=tmp_path,
        )


def test_index_unlisted_index_fails(tmp_path: Path) -> None:
    fetch = FakeIndex({})
    with pytest.raises(Mismatch, match="not an allowed index"):
        check_index(
            EXPECTED,
            "https://example.invalid",
            "perceivo",
            "1.0.0rc1",
            tmp_path / "s",
            fetch,
        )


@pytest.mark.parametrize("which", ["sums", "dist"])
@pytest.mark.parametrize("escape", ["dotdot", "absolute"])
def test_sums_paths_outside_root_are_refused(tmp_path: Path, which: str, escape: str) -> None:
    dist = _dist(tmp_path)
    sums = _sums(tmp_path, dist)
    root = tmp_path / "root"
    root.mkdir()
    # Both inputs are valid; only their location is wrong.
    inside_dist = root / "dist"
    shutil.copytree(dist, inside_dist)
    inside_sums = root / "SHA256SUMS"
    shutil.copy(sums, inside_sums)
    outside = {"sums": sums, "dist": dist}[which]
    bad = Path("..") / outside.name if escape == "dotdot" else outside
    args = {"sums": inside_sums, "dist": inside_dist}
    args[which] = bad
    assert check_sums(inside_dist, inside_sums, root)  # control: inside passes
    with pytest.raises(Mismatch, match="outside the allowed root"):
        check_sums(args["dist"], args["sums"], root)


def test_index_download_dir_outside_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    fetch = FakeIndex({WHEEL: b"wheel-bytes", SDIST: b"sdist-bytes"})
    with pytest.raises(Mismatch, match="outside the allowed root"):
        check_index(
            EXPECTED,
            "https://test.pypi.org",
            "perceivo",
            "1.0.0rc1",
            Path("../served"),
            fetch,
            root=root,
        )
    assert not (tmp_path / "served").exists()


def test_sums_cli_refuses_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.chdir(root)
    dist = _dist(tmp_path)
    sums = _sums(tmp_path, dist)
    assert main(["sums", "--dist", str(dist), "--sums", str(sums)]) == 1


# --------------------------------------------------------------------------- #
# The build job's outputs are the only trusted digests
# --------------------------------------------------------------------------- #


def test_digests_prints_exactly_the_four_output_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dist = _dist(tmp_path)
    sums = _sums(tmp_path, dist)
    assert main(["digests", "--dist", "dist", "--sums", "SHA256SUMS"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"wheel_name={WHEEL}",
        f"wheel_sha256={EXPECTED[WHEEL]}",
        f"sdist_name={SDIST}",
        f"sdist_sha256={EXPECTED[SDIST]}",
    ]
    assert digests(dist, sums, tmp_path) == out


def test_digests_refuses_files_that_do_not_match_sums(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    sums = _sums(tmp_path, dist)
    (dist / SDIST).write_bytes(b"sdist-bytez")
    with pytest.raises(Mismatch, match="sha256 differs"):
        digests(dist, sums, tmp_path)


def test_replaced_artifact_and_sums_still_fail_against_build_outputs(tmp_path: Path) -> None:
    # A later job replaced both the distributions and their SHA256SUMS, and
    # uploaded the replacement: the pair agrees with itself...
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(b"attacker-wheel")
    (dist / SDIST).write_bytes(b"sdist-bytes")
    assert check_sums(dist, _sums(tmp_path, dist), tmp_path)
    # ...but the index is compared with the build job's outputs, not with them.
    fetch = FakeIndex({WHEEL: b"attacker-wheel", SDIST: b"sdist-bytes"})
    served = Path("s")
    with pytest.raises(Mismatch, match=f"sha256 differs for {WHEEL}"):
        check_index(
            EXPECTED,
            "https://test.pypi.org",
            "perceivo",
            "1.0.0rc1",
            served,
            fetch,
            root=tmp_path,
        )


@pytest.mark.parametrize(
    "values",
    [
        [f"{WHEEL}={EXPECTED[WHEEL]}"],
        [f"{WHEEL}={EXPECTED[WHEEL]}", f"{WHEEL}={EXPECTED[WHEEL]}"],
        [f"{WHEEL}={EXPECTED[WHEEL]}", "perceivo-1.0.0rc1-py311-none-any.whl=" + "a" * 64],
        [f"{WHEEL}={EXPECTED[WHEEL]}", f"{SDIST}=" + "A" * 64],
        [f"{WHEEL}={EXPECTED[WHEEL]}", f"{SDIST}"],
        [f"{WHEEL}={EXPECTED[WHEEL]}", f"../{SDIST}={EXPECTED[SDIST]}"],
        [f"{WHEEL}={EXPECTED[WHEEL]}", f"{SDIST}={EXPECTED[SDIST][:63]}"],
        [f"{WHEEL}={EXPECTED[WHEEL]}", f"{SDIST}={EXPECTED[SDIST]}", "x.tar.gz=" + "b" * 64],
    ],
)
def test_parse_expected_refuses_malformed(values: list[str]) -> None:
    with pytest.raises(Mismatch):
        parse_expected(values)


def test_parse_expected_accepts_the_build_outputs() -> None:
    assert parse_expected([f"{WHEEL}={EXPECTED[WHEEL]}", f"{SDIST}={EXPECTED[SDIST]}"]) == EXPECTED


def test_index_cli_refuses_malformed_expect_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(url: str) -> bytes:
        raise AssertionError(f"fetched {url}")

    monkeypatch.setattr(compare, "_default_fetch", no_network)
    argv = [
        "index",
        "--index-url",
        "https://test.pypi.org",
        "--version",
        "1.0.0rc1",
        "--download-dir",
        "served",
        "--expect",
        f"{WHEEL}=not-a-digest",
        "--expect",
        f"{SDIST}={EXPECTED[SDIST]}",
    ]
    assert main(argv) == 1


def test_confine_refuses_sibling_prefix_directory(tmp_path: Path) -> None:
    # "<root>-evil" starts with the root's own name; only the separator
    # boundary keeps it out.
    root = tmp_path / "base"
    root.mkdir()
    (tmp_path / "base-evil").mkdir()
    sibling = Path("../base-evil/x")
    with pytest.raises(Mismatch, match="outside the allowed root"):
        compare.confine(sibling, root)
    assert compare.confine(Path("x"), root) == (root / "x").resolve()


def test_parse_expected_refuses_a_name_given_twice() -> None:
    # Two digests for one wheel name: the second must not silently win.
    values = [f"{WHEEL}={EXPECTED[WHEEL]}", f"{WHEEL}=" + "c" * 64, f"{SDIST}={EXPECTED[SDIST]}"]
    with pytest.raises(Mismatch, match="twice"):
        parse_expected(values)
