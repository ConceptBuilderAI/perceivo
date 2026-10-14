"""``perceivo.files.write`` resolves an omitted media type before the host sees it.

A hosted extension manager forwards ``mime_type`` to the upload as the part's
declared type, and the data service stores whatever is declared. ``None`` lets
the HTTP client fall back to ``application/octet-stream`` for an unknown
suffix, so the type is resolved in the SDK, where a local run resolves it too.
"""

from __future__ import annotations

from typing import Any

import pytest

import perceivo
from perceivo._internal.context import _ExecutionContext


class _RecordingOrgFiles:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    async def write(self, **kwargs: Any) -> dict[str, Any]:
        self.writes.append(kwargs)
        return {"entry": {"id": "e1"}, "version": {}}


class _HostedExt:
    def __init__(self) -> None:
        self.org_files = _RecordingOrgFiles()


@pytest.fixture
def hosted():
    _ExecutionContext.clear_all()
    ext = _HostedExt()
    _ExecutionContext.set_ext(ext)
    yield ext.org_files
    _ExecutionContext.clear_all()


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [("notes", "text/plain"), ("a.json", "application/json")],
)
async def test_an_omitted_type_reaches_the_host_resolved(hosted, file_name, expected):
    await perceivo.files.write(file_name, "x")
    assert hosted.writes[-1]["mime_type"] == expected


async def test_an_explicit_type_reaches_the_host_unchanged(hosted):
    await perceivo.files.write("a.json", "x", mime_type="text/csv")
    assert hosted.writes[-1]["mime_type"] == "text/csv"
