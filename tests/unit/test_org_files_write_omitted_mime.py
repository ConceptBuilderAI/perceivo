"""A hosted ``write`` without a ``mime_type`` declares the type its name resolves to."""

from __future__ import annotations

from perceivo._internal import mime as mime_mod

from .test_org_files_mime import _upload_service


async def test_the_omitted_type_is_resolved_from_the_name(monkeypatch):
    monkeypatch.setattr(
        mime_mod.mimetypes, "guess_type", lambda name: ("application/x-sentinel", None)
    )
    svc = _upload_service()
    await svc.write("anything.whatever", b"payload")
    assert svc._client.calls[-1][2]["files"]["file"][2] == "application/x-sentinel"


async def test_an_unresolvable_name_is_declared_as_text_plain(monkeypatch):
    monkeypatch.setattr(mime_mod.mimetypes, "guess_type", lambda name: (None, None))
    svc = _upload_service()
    await svc.write("anything.whatever", b"payload")
    assert svc._client.calls[-1][2]["files"]["file"][2] == "text/plain"


async def test_an_explicit_none_takes_the_same_rule(monkeypatch):
    monkeypatch.setattr(mime_mod.mimetypes, "guess_type", lambda name: ("text/csv", None))
    svc = _upload_service()
    await svc.write("rows.csv", b"a,b", mime_type=None)
    assert svc._client.calls[-1][2]["files"]["file"][2] == "text/csv"
