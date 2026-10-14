"""Unit tests for perceivo.bootstrap.* (egress-broker-backed services).

The HTTP boundary (httpx.AsyncClient) is mocked; no network is touched.
"""

from __future__ import annotations

import pytest

from perceivo._internal.context import _ExecutionContext
from perceivo.bootstrap._files import FilesService
from perceivo.bootstrap._logging import LoggingService
from perceivo.bootstrap._org_files import OrgFilesService, _check_response
from perceivo.bootstrap._secrets import SecretsService
from perceivo.bootstrap._session import Session, connect, disconnect

# ---------------------------------------------------------------------------
# Fake httpx primitives
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.content = content
        self.text = text
        self.url = "http://egress/test"

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Records calls and returns queued responses keyed by HTTP verb."""

    def __init__(self, responses):
        self._responses = responses  # dict: verb -> list[FakeResponse]
        self.calls = []

    async def _dispatch(self, verb, url, **kwargs):
        self.calls.append((verb, url, kwargs))
        queue = self._responses.get(verb, [])
        if queue:
            return queue.pop(0)
        return FakeResponse(200, {})

    async def get(self, url, **kwargs):
        return await self._dispatch("get", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._dispatch("post", url, **kwargs)

    async def patch(self, url, **kwargs):
        return await self._dispatch("patch", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self._dispatch("delete", url, **kwargs)


def _make_org_service(responses):
    svc = OrgFilesService(
        egress_url="http://egress", execution_token="tok", organization_id="org-1"
    )
    svc._client = FakeClient(responses)
    return svc


# ---------------------------------------------------------------------------
# _check_response
# ---------------------------------------------------------------------------


def test_check_response_ok():
    _check_response(FakeResponse(200))  # no raise


def test_check_response_friendly_errors():
    for code in (502, 503, 504):
        response = FakeResponse(code)
        with pytest.raises(RuntimeError):
            _check_response(response)


def test_check_response_404_file_not_found():
    response = FakeResponse(404)
    with pytest.raises(FileNotFoundError):
        _check_response(response)


def test_check_response_generic_error():
    response = FakeResponse(500, text="boom")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _check_response(response)


# ---------------------------------------------------------------------------
# OrgFilesService
# ---------------------------------------------------------------------------


async def test_org_list_resolves_space_and_filters():
    svc = _make_org_service(
        {
            "get": [
                FakeResponse(200, [{"id": "space-1"}]),  # spaces
                FakeResponse(
                    200,
                    {
                        "items": [
                            {"id": "f1", "name": "a.txt", "entry_type": "file"},
                            {"id": "d1", "name": "docs", "entry_type": "folder"},
                        ]
                    },
                ),  # contents
            ]
        }
    )
    files = await svc.list(entry_type="file")
    assert [f["name"] for f in files] == ["a.txt"]
    assert svc._default_space_id == "space-1"


async def test_org_list_no_spaces_raises():
    svc = _make_org_service({"get": [FakeResponse(200, [])]})
    with pytest.raises(RuntimeError, match="No storage spaces"):
        await svc.list()


async def test_org_create_folder():
    svc = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(200, {"id": "folder-9", "name": "reports"})],
        }
    )
    result = await svc.create_folder("reports")
    assert result["id"] == "folder-9"


async def test_org_rename_move_delete():
    svc = _make_org_service(
        {
            "patch": [FakeResponse(200, {"id": "e1", "name": "new.txt"})],
            "post": [FakeResponse(200, {"id": "e1", "name": "new.txt"})],
            "delete": [FakeResponse(200, {})],
        }
    )
    assert (await svc.rename("e1", "new.txt"))["name"] == "new.txt"
    assert (await svc.move("e1", "p2"))["id"] == "e1"
    await svc.delete("e1")  # no raise


async def test_org_read_via_broker():
    svc = _make_org_service({"get": [FakeResponse(200, content=b"abc")]})
    data = await svc.read("entry-1")
    assert data == b"abc"
    verb, url, _kwargs = svc._client.calls[-1]
    assert verb == "get"
    assert url == "http://egress/internal/org-files/entries/entry-1/download"


async def test_org_read_not_found():
    svc = _make_org_service({"get": [FakeResponse(404, text="nf")]})
    with pytest.raises(FileNotFoundError):
        await svc.read("missing")


async def test_org_write_via_broker():
    svc = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],  # space
            "post": [
                FakeResponse(
                    200,
                    {"entry": {"id": "new-entry"}, "version": {"version_number": 1}},
                )
            ],
        }
    )
    result = await svc.write_text("out.txt", "hello")
    # The platform data service's UploadResponse, relayed by the broker unchanged.
    assert result["entry"]["id"] == "new-entry"
    verb, url, kwargs = svc._client.calls[-1]
    assert verb == "post"
    assert url == "http://egress/internal/org-files/spaces/space-1/upload"
    assert kwargs["files"] == {"file": ("out.txt", b"hello", "text/plain")}


# ---------------------------------------------------------------------------
# FilesService (path-based over org files)
# ---------------------------------------------------------------------------


class _FakeOrg:
    """In-memory stand-in for OrgFilesService."""

    def __init__(self):
        self.tree = {}  # parent_id -> list of entries
        self.contents = {}  # entry_id -> bytes
        self._counter = 0

    async def list(self, parent_id=None, **kwargs):
        return list(self.tree.get(parent_id, []))

    async def create_folder(self, name, parent_id=None, **kwargs):
        self._counter += 1
        eid = f"folder-{self._counter}"
        self.tree.setdefault(parent_id, []).append(
            {"id": eid, "name": name, "entry_type": "folder"}
        )
        return {"id": eid}

    async def write(self, file_name, content, parent_id=None, **kwargs):
        self._counter += 1
        eid = f"file-{self._counter}"
        self.tree.setdefault(parent_id, []).append(
            {"id": eid, "name": file_name, "entry_type": "file"}
        )
        self.contents[eid] = content
        # Mirrors the real UploadResponse shape: {"entry": ..., "version": ...}
        return {"entry": {"id": eid, "name": file_name}, "version": {"version_number": 1}}

    async def read(self, entry_id):
        return self.contents[entry_id]


async def test_files_write_creates_folders_and_reads_back():
    org = _FakeOrg()
    svc = FilesService(org)
    entry_id = await svc.write("data", "models/sentiment/model.txt")
    assert entry_id.startswith("file-")
    content = await svc.read("models/sentiment/model.txt")
    assert content == "data"


async def test_files_write_root_level():
    org = _FakeOrg()
    svc = FilesService(org)
    eid = await svc.write(b"bytes", "top.txt")
    assert eid


async def test_files_read_missing_raises():
    org = _FakeOrg()
    svc = FilesService(org)
    with pytest.raises(FileNotFoundError):
        await svc.read("nope/ghost.txt")


async def test_files_ensure_folder_uses_cache():
    org = _FakeOrg()
    svc = FilesService(org)
    first = await svc._ensure_folder("a/b")
    second = await svc._ensure_folder("a/b")  # cache hit
    assert first == second


# ---------------------------------------------------------------------------
# SecretsService
# ---------------------------------------------------------------------------


def _make_secrets(responses):
    svc = SecretsService(egress_url="http://egress", execution_token="tok")
    svc._client = FakeClient(responses)
    return svc


async def test_secrets_fetch_success():
    svc = _make_secrets({"get": [FakeResponse(200, {"value": "s3cr3t"})]})
    assert await svc.fetch_secret_value("sid") == "s3cr3t"


async def test_secrets_fetch_404_and_403_return_none():
    svc = _make_secrets({"get": [FakeResponse(404)]})
    assert await svc.fetch_secret_value("sid") is None
    svc2 = _make_secrets({"get": [FakeResponse(403)]})
    assert await svc2.fetch_secret_value("sid") is None


async def test_secrets_fetch_exception_returns_none():
    svc = _make_secrets({"get": [FakeResponse(500)]})
    assert await svc.fetch_secret_value("sid") is None


# ---------------------------------------------------------------------------
# LoggingService
# ---------------------------------------------------------------------------


async def test_notebook_logging_write_and_conversation():
    svc = LoggingService()
    await svc.write("info", "msg", metadata={"k": "v"})
    await svc.write("error", "bad")
    assert await svc.get_conversation_logs() == []


# ---------------------------------------------------------------------------
# Session + connect/disconnect
# ---------------------------------------------------------------------------


def test_session_activate_deactivate():
    session = Session("http://egress", "tok", "org-1")
    session.activate()
    assert _ExecutionContext.get_ext() is session
    assert session.get_node_secret_info() == {"declarations": {}, "aliases": {}}
    session.deactivate()
    with pytest.raises(RuntimeError):
        _ExecutionContext.get_ext()


def test_connect_local_mode(tmp_path, monkeypatch):
    cfg = tmp_path / "perceivo.yaml"
    cfg.write_text("version: 1\n", encoding="utf-8")
    ext = connect(config_path=str(cfg))
    try:
        from perceivo.local.context import LocalExtensionManager

        assert isinstance(ext, LocalExtensionManager)
    finally:
        disconnect()


def test_connect_platform_requires_env(monkeypatch):
    monkeypatch.delenv("EGRESS_BROKER_URL", raising=False)
    monkeypatch.delenv("EXECUTION_TOKEN", raising=False)
    monkeypatch.delenv("ORGANIZATION_ID", raising=False)
    with pytest.raises(RuntimeError, match="EGRESS_BROKER_URL not set"):
        connect()


def test_connect_platform_with_env(monkeypatch):
    monkeypatch.setenv("EGRESS_BROKER_URL", "http://egress/")
    monkeypatch.setenv("EXECUTION_TOKEN", "tok")
    monkeypatch.setenv("ORGANIZATION_ID", "org-7")
    session = connect()
    try:
        assert isinstance(session, Session)
        assert session.organization_id == "org-7"
    finally:
        disconnect()
