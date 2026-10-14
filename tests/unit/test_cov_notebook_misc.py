"""Coverage-focused unit tests for three SDK modules.

Targets:
    * perceivo.bootstrap._org_files        (broker HTTP client, error mappings)
    * perceivo.files._org                  (service-unavailable branches, find)
    * perceivo.component.registry          (source resolution, file discovery)

No network is touched: the httpx boundary and git subprocess are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from perceivo.bootstrap._org_files import OrgFilesService
from perceivo.files import _org as org_storage

# ===========================================================================
# Fake httpx primitives (self-contained; mirrors test_notebook.py)
# ===========================================================================


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.content = content
        self.text = text
        self.url = "http://egress/test"

    def json(self):
        return self._json


class FakeClient:
    """Records calls and returns queued responses keyed by HTTP verb."""

    def __init__(self, responses):
        self._responses = responses
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


# ===========================================================================
# perceivo.bootstrap._org_files
# ===========================================================================


async def test_get_space_id_explicit_and_cached():
    svc = _make_org_service({})
    # Explicit id short-circuits, no HTTP.
    assert await svc._get_space_id("explicit-1") == "explicit-1"
    # Cached default short-circuits too.
    svc._default_space_id = "cached-9"
    assert await svc._get_space_id() == "cached-9"


async def test_list_with_parent_id_no_filter():
    svc = _make_org_service(
        {
            "get": [
                FakeResponse(200, [{"id": "space-1"}]),
                FakeResponse(
                    200,
                    {
                        "items": [
                            {"id": "f1", "name": "a", "entry_type": "file"},
                            {"id": "d1", "name": "b", "entry_type": "folder"},
                        ]
                    },
                ),
            ]
        }
    )
    items = await svc.list(parent_id="parent-7")
    assert len(items) == 2
    # parent_id propagated as a query param.
    contents_call = svc._client.calls[-1]
    assert contents_call[2]["params"]["parent_id"] == "parent-7"


async def test_read_downloads_via_broker():
    svc = _make_org_service({"get": [FakeResponse(200, content=b"abc")]})
    assert await svc.read("entry-1") == b"abc"
    verb, url, _kwargs = svc._client.calls[-1]
    assert verb == "get"
    assert url == "http://egress/internal/org-files/entries/entry-1/download"


async def test_read_text_decodes():
    svc = _make_org_service({"get": [FakeResponse(200, content="héllo".encode())]})
    assert await svc.read_text("e1") == "héllo"


async def test_read_friendly_error():
    svc = _make_org_service({"get": [FakeResponse(503, text="down")]})
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await svc.read("e1")


async def test_read_unreachable_error():
    svc = _make_org_service({"get": [FakeResponse(502, text="bad gw")]})
    with pytest.raises(RuntimeError, match="temporarily unreachable"):
        await svc.read("e1")


async def test_read_generic_error():
    svc = _make_org_service({"get": [FakeResponse(500, text="oops")]})
    with pytest.raises(RuntimeError, match="File operation failed"):
        await svc.read("e1")


async def test_write_coerces_none_mime_and_posts_multipart():
    svc = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(200, {"entry": {"id": "entry-99"}, "version": {}})],
        }
    )
    result = await svc.write("data.bin", bytes([0, 1]), mime_type=None)
    assert result["entry"]["id"] == "entry-99"
    verb, url, kwargs = svc._client.calls[-1]
    assert verb == "post"
    assert url == "http://egress/internal/org-files/spaces/space-1/upload"
    # Field name must be `file`: the platform data service's route declares
    # `file: Annotated[UploadFile, File()]`, so any other name is a 422.
    # No mime_type given: ``.bin`` resolves to application/octet-stream by suffix.
    assert kwargs["files"] == {"file": ("data.bin", bytes([0, 1]), "application/octet-stream")}
    # No parent_id given -> no form field, and nothing on the query string.
    assert kwargs["data"] == {}
    assert "params" not in kwargs


async def test_write_sends_parent_id_as_form_field():
    svc = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(200, {"entry": {"id": "e"}, "version": {}})],
        }
    )
    await svc.write("f.txt", b"abc", parent_id="parent-7", mime_type="text/plain")
    kwargs = svc._client.calls[-1][2]
    # parent_id is a form field on the upload route, and the platform never reads
    # a form field from the query string.
    assert kwargs["data"] == {"parent_id": "parent-7"}
    assert "params" not in kwargs
    assert kwargs["files"]["file"][2] == "text/plain"


async def test_write_parent_id_reaches_the_wire_in_the_multipart_body():
    """The fake above records kwargs; this one builds the real httpx request."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": "space-1"}])
        return httpx.Response(200, json={"entry": {"id": "e"}, "version": {}})

    svc = OrgFilesService(
        egress_url="http://egress", execution_token="tok", organization_id="org-1"
    )
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await svc.write("f.txt", b"abc", parent_id="parent-7", mime_type="text/plain")
    upload = seen[-1]
    assert upload.method == "POST"
    assert "parent_id" not in upload.url.params
    body = upload.read()
    assert b'name="parent_id"\r\n\r\nparent-7\r\n' in body
    assert b'name="file"; filename="f.txt"' in body


async def test_write_friendly_error():
    svc = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(504, text="timeout")],
        }
    )
    with pytest.raises(RuntimeError, match="timed out"):
        await svc.write("f.txt", b"abc")


async def test_write_generic_error():
    svc = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(500, text="err")],
        }
    )
    with pytest.raises(RuntimeError, match="File operation failed"):
        await svc.write("f.txt", b"abc")


async def test_transfers_open_no_second_http_client(monkeypatch):
    """The SDK opens no TLS connection of its own.

    Every byte moves over ``self._client``, which addresses the egress
    broker. A direct-to-blob transfer would have to build a fresh
    ``httpx.AsyncClient`` inside this module, so make that construction
    fail: the patch is installed after the service exists, which is the
    only client the SDK is allowed to have.
    """
    svc = _make_org_service(
        {
            "get": [
                FakeResponse(200, [{"id": "space-1"}]),
                FakeResponse(200, content=b"x"),
            ],
            "post": [FakeResponse(200, {"entry": {"id": "e"}, "version": {}})],
        }
    )

    def _boom(*a, **k):
        raise AssertionError("_org_files must not construct its own httpx client")

    monkeypatch.setattr("perceivo.bootstrap._org_files.httpx.AsyncClient", _boom)
    await svc.write("f.txt", b"abc")
    assert await svc.read("e") == b"x"


async def test_check_response_direct_paths():
    from perceivo.bootstrap._org_files import _check_response

    _check_response(FakeResponse(200))  # no raise
    resp_404 = FakeResponse(404)
    with pytest.raises(FileNotFoundError):
        _check_response(resp_404)
    resp_500 = FakeResponse(500, text="boom")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _check_response(resp_500)


async def test_list_root_and_entry_filter():
    svc = _make_org_service(
        {
            "get": [
                FakeResponse(200, [{"id": "space-1"}]),
                FakeResponse(
                    200,
                    {
                        "items": [
                            {"id": "f1", "name": "a", "entry_type": "file"},
                            {"id": "d1", "name": "b", "entry_type": "folder"},
                        ]
                    },
                ),
            ]
        }
    )
    files = await svc.list(entry_type="file")  # no parent_id -> root branch
    assert [f["name"] for f in files] == ["a"]


async def test_list_no_spaces_raises():
    svc = _make_org_service({"get": [FakeResponse(200, [])]})
    with pytest.raises(RuntimeError, match="No storage spaces"):
        await svc.list()


async def test_read_not_found():
    svc = _make_org_service({"get": [FakeResponse(404, text="nf")]})
    with pytest.raises(FileNotFoundError):
        await svc.read("missing")


async def test_create_rename_delete_move_write_text():
    svc = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(200, {"id": "folder-1", "name": "reports"})],
            "patch": [FakeResponse(200, {"id": "e1", "name": "new.txt"})],
            "delete": [FakeResponse(200, {})],
        }
    )
    assert (await svc.create_folder("reports"))["id"] == "folder-1"
    assert (await svc.rename("e1", "new.txt"))["name"] == "new.txt"
    await svc.delete("e1", permanent=True)

    svc2 = _make_org_service({"post": [FakeResponse(200, {"id": "e1", "name": "moved"})]})
    assert (await svc2.move("e1", "p2"))["id"] == "e1"

    # write_text -> write -> one multipart POST through the broker.
    svc3 = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(200, {"entry": {"id": "wt-entry"}, "version": {}})],
        }
    )
    assert (await svc3.write_text("t.txt", "hi"))["entry"]["id"] == "wt-entry"
    assert svc3._client.calls[-1][2]["files"]["file"][2] == "text/plain"


async def test_write_upload_unreachable():
    # The single upload POST fails -> _check_response maps 502 at the call site.
    svc = _make_org_service(
        {
            "get": [FakeResponse(200, [{"id": "space-1"}])],
            "post": [FakeResponse(502, text="gw")],
        }
    )
    with pytest.raises(RuntimeError, match="temporarily unreachable"):
        await svc.write("f.txt", b"abc")


# ===========================================================================
# perceivo.files._org — service-unavailable branches
# ===========================================================================

_UNAVAIL = "Organization files service not available"


async def test_org_storage_functions_raise_when_unavailable(stub_ext):
    stub_ext.org_files = None
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.list()
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.read("e1")
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.read_text("e1")
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.write("f", "c")
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.write_text("f", "c")
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.mkdir("d")
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.rename("e1", "n")
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.delete("e1")
    with pytest.raises(RuntimeError, match=_UNAVAIL):
        await org_storage.move("e1", "p")


def _entry(**over: object) -> dict:
    """One org-storage listing entry, every key the producer sends.

    Mirrors ``perceivo/local/adapters/org_files.py``::``_entry``, which
    emits all ten unconditionally. A partial dict here would only prove
    that a partial dict was accepted.
    """
    base = {
        "id": "e-1",
        "name": "entry.txt",
        "path": "/entry.txt",
        "entry_type": "file",
        "parent_id": None,
        "mime_type": "text/plain",
        "size_bytes": 3,
        "blob_name": None,
        "created_at": "2026-08-19T00:00:00Z",
        "updated_at": "2026-08-19T00:00:00Z",
    }
    return base | over


async def test_org_storage_delegates_to_adapter(stub_ext):
    adapter = AsyncMock()
    adapter.list.return_value = [_entry(id="1", name="one.txt")]
    adapter.read.return_value = b"bytes"
    adapter.read_text.return_value = "text"
    adapter.write.return_value = {"id": "w"}
    adapter.write_text.return_value = {"id": "wt"}
    adapter.create_folder.return_value = {"id": "cf"}
    adapter.rename.return_value = {"id": "rn"}
    adapter.move.return_value = {"id": "mv"}
    adapter.delete.return_value = None
    stub_ext.org_files = adapter

    listed = await org_storage.list(parent_id="p", entry_type="file")
    assert [e.id for e in listed] == ["1"]
    assert await org_storage.read("e") == b"bytes"
    assert await org_storage.read_text("e", encoding="ascii") == "text"
    assert (await org_storage.write("f", "c"))["id"] == "w"
    assert (await org_storage.write_text("f", "c"))["id"] == "wt"
    assert (await org_storage.mkdir("d"))["id"] == "cf"
    assert (await org_storage.rename("e", "n"))["id"] == "rn"
    assert (await org_storage.move("e", "p"))["id"] == "mv"
    await org_storage.delete("e", permanent=True)
    adapter.delete.assert_awaited_with("e", permanent=True)
    adapter.create_folder.assert_awaited_with(folder_name="d", parent_id=None, space_id=None)


async def test_org_storage_find_native(stub_ext):
    adapter = AsyncMock()
    adapter.find.return_value = _entry(id="hit", name="x")
    stub_ext.org_files = adapter
    result = await org_storage.find("x", space_id="s1")
    assert result.id == "hit"
    adapter.find.assert_awaited_with(name="x", space_id="s1")


async def test_org_storage_find_fallback_scan(stub_ext):
    class _NoFindOrg:
        # No `find` attribute -> triggers the top-level scan fallback.
        async def list(self, space_id=None):
            return [_entry(id="o", name="other"), _entry(id="t", name="target")]

    stub_ext.org_files = _NoFindOrg()
    hit = await org_storage.find("target")
    assert getattr(hit, "name", None) == "target"
    miss = await org_storage.find("absent")
    assert miss is None


async def test_org_storage_find_none_when_unavailable(stub_ext):
    stub_ext.org_files = None
    assert await org_storage.find("anything") is None


async def test_org_storage_find_none_outside_context(cleared_context):
    # get_ext() raises RuntimeError with no ext on the stack -> find returns None.
    assert await org_storage.find("anything") is None


# ===========================================================================
# perceivo.component.registry
# ===========================================================================
