"""``write_text`` must not override the host's own media-type detection.

The platform applies **no allowlist** on the org-files upload route. It resolves

    mime_type = file.content_type or mimetypes.guess_type(file.filename)[0]
                or "application/octet-stream"

and persists whatever comes out. So a media type the SDK declares is believed
without question: a fixed ``mime_type="text/plain"`` would not be a default the
host could correct -- it would *replace* the host's own guess, and every
``.json`` file written with ``write_text`` would be stored as plain text;
sending nothing would be strictly more accurate.

These tests pin the rule, not one platform's table. ``mimetypes.guess_type``
consults the Windows registry as well as the built-in map, so ``.csv`` answers
``text/csv`` on Linux CI and ``application/vnd.ms-excel`` on a Windows box.
Only suffixes that agree across both are asserted literally; the wiring itself
is proved by monkeypatching ``guess_type`` and watching the sentinel arrive at
the multipart part, which holds on any platform.
"""

from __future__ import annotations

import mimetypes

import pytest

from perceivo._internal import mime as mime_mod
from perceivo.local.adapters.org_files import LocalOrgFilesService

from .test_cov_notebook_misc import FakeResponse, _make_org_service

# (file name, expected media type). Every entry is stable on Linux and Windows:
# .txt and the suffixless name are the two that must NOT change, and .json is
# the defect itself.
_STABLE = [
    ("report.json", "application/json"),
    ("page.html", "text/html"),
    ("logo.png", "image/png"),
    ("notes.txt", "text/plain"),
    ("LICENSE", "text/plain"),
    ("archive.tar.gz", "application/x-tar"),
]


def _upload_service():
    return _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(200, {"entry": {"id": "e1"}, "version": {}})],
        }
    )


@pytest.mark.parametrize(("file_name", "expected"), _STABLE)
def test_the_resolver_agrees_with_the_hosts_own_guess(file_name, expected):
    """The SDK's answer is ``mimetypes.guess_type``'s answer, not a constant."""
    if mimetypes.guess_type(file_name)[0] not in (expected, None):
        pytest.skip(f"platform mimetypes table disagrees for {file_name}")
    assert mime_mod.guess_text_mime(file_name) == expected


def test_an_unresolvable_suffix_falls_back_to_text_not_octet_stream():
    """``write_text`` takes ``str``, so text is the right fallback here.

    The host's own fallback is ``application/octet-stream``; this is the one
    deliberate divergence, and it is what keeps suffixless names stored as
    ``text/plain``.
    """
    assert mime_mod.guess_text_mime("no-suffix-at-all") == "text/plain"
    assert mime_mod.TEXT_FALLBACK == "text/plain"


@pytest.mark.parametrize(("file_name", "expected"), _STABLE)
async def test_write_text_declares_the_resolved_type_in_cluster(file_name, expected):
    """End to end through the real client: the multipart part carries it."""
    if mimetypes.guess_type(file_name)[0] not in (expected, None):
        pytest.skip(f"platform mimetypes table disagrees for {file_name}")
    svc = _upload_service()
    await svc.write_text(file_name, "payload")
    part = svc._client.calls[-1][2]["files"]["file"]
    assert part[0] == file_name
    assert part[2] == expected


async def test_the_declared_type_is_whatever_guess_type_returns(monkeypatch):
    """Platform-independent proof of the wiring, not of one mime table.

    A sentinel no table would ever produce reaches the multipart part, so this
    fails if the value is ever recomputed, defaulted or overridden downstream.
    """
    monkeypatch.setattr(
        mime_mod.mimetypes, "guess_type", lambda name: ("application/x-sentinel", None)
    )
    svc = _upload_service()
    await svc.write_text("anything.whatever", "payload")
    assert svc._client.calls[-1][2]["files"]["file"][2] == "application/x-sentinel"


async def test_a_guess_of_none_still_reaches_the_host_as_text_plain(monkeypatch):
    """The fallback is applied in the SDK, so the host never sees ``None``."""
    monkeypatch.setattr(mime_mod.mimetypes, "guess_type", lambda name: (None, None))
    svc = _upload_service()
    await svc.write_text("anything.whatever", "payload")
    assert svc._client.calls[-1][2]["files"]["file"][2] == "text/plain"


async def test_write_still_honours_an_explicit_mime_type():
    """The escape hatch is untouched: ``write`` wins over any suffix."""
    svc = _upload_service()
    await svc.write("report.json", b"{}", mime_type="text/plain")
    assert svc._client.calls[-1][2]["files"]["file"][2] == "text/plain"


@pytest.mark.parametrize(("file_name", "expected"), _STABLE)
async def test_local_write_text_records_what_the_host_would_record(tmp_path, file_name, expected):
    """Local parity: the entry carries the type the host records, not ``None``."""
    if mimetypes.guess_type(file_name)[0] not in (expected, None):
        pytest.skip(f"platform mimetypes table disagrees for {file_name}")
    svc = LocalOrgFilesService(org_dir=str(tmp_path))
    result = await svc.write_text(file_name, "payload")
    assert result["entry"]["mime_type"] == expected
