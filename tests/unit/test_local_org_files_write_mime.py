"""A local ``write`` without ``mime_type`` detects it from the suffix, else text/plain."""

from __future__ import annotations

import pytest

from perceivo._internal import mime as mime_mod
from perceivo.local.adapters.org_files import LocalOrgFilesService


async def test_an_omitted_mime_type_comes_from_the_suffix(tmp_path, monkeypatch):
    seen: list[str] = []

    def _guess(name):
        seen.append(name)
        return ("application/x-sentinel", None)

    monkeypatch.setattr(mime_mod.mimetypes, "guess_type", _guess)
    out = await LocalOrgFilesService(str(tmp_path)).write("report.xyz", b"x")
    assert out["entry"]["mime_type"] == "application/x-sentinel"
    assert seen == ["report.xyz"]


async def test_an_unrecognised_suffix_falls_back_to_text_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(mime_mod.mimetypes, "guess_type", lambda name: (None, None))
    out = await LocalOrgFilesService(str(tmp_path)).write("notes", b"x")
    assert out["entry"]["mime_type"] == "text/plain"


@pytest.mark.parametrize("given", ["application/pdf", "text/csv"])
async def test_an_explicit_mime_type_is_kept(tmp_path, given):
    out = await LocalOrgFilesService(str(tmp_path)).write("report.json", b"{}", mime_type=given)
    assert out["entry"]["mime_type"] == given
