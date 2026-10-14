"""Unit tests for perceivo.local.adapters.* (filesystem-backed, no network).

The ``InMemoryRedis`` cases live in ``test_local_redis_adapter.py``.
"""

from __future__ import annotations

import pytest

from perceivo.local.adapters.files import LocalFilesService
from perceivo.local.adapters.logging import LocalLoggingService
from perceivo.local.adapters.org_files import LocalOrgFilesService
from perceivo.local.adapters.secrets import LocalSecretsService, _load_dotenv

# ---------------------------------------------------------------------------
# LocalFilesService
# ---------------------------------------------------------------------------


async def test_files_write_read_roundtrip(tmp_path):
    svc = LocalFilesService(working_dir=str(tmp_path / "wf"))
    path = await svc.write("hello", "out/result.txt")
    assert path.endswith("result.txt")
    assert await svc.read("out/result.txt") == "hello"


async def test_files_write_bytes(tmp_path):
    svc = LocalFilesService(working_dir=str(tmp_path / "wf"))
    await svc.write(b"\x00\x01", "blob.bin")
    # read() decodes utf-8; just ensure the file exists via list_blobs
    blobs = await svc.list_blobs("", "blob", limit=10)
    assert any(b["name"] == "blob.bin" for b in blobs)


async def test_files_read_missing_raises(tmp_path):
    svc = LocalFilesService(working_dir=str(tmp_path / "wf"))
    with pytest.raises(FileNotFoundError):
        await svc.read("ghost.txt")


async def test_files_list_blobs_and_read_blob(tmp_path):
    svc = LocalFilesService(working_dir=str(tmp_path / "wf"))
    root = tmp_path / "wf" / "cont"
    root.mkdir(parents=True)
    (root / "a.txt").write_text("aa", encoding="utf-8")
    (root / "b.txt").write_text("bb", encoding="utf-8")
    blobs = await svc.list_blobs("cont", "a", limit=10)
    assert [b["name"] for b in blobs] == ["a.txt"]
    assert await svc.read_blob("cont", "a.txt") == "aa"


async def test_files_list_blobs_missing_container(tmp_path):
    svc = LocalFilesService(working_dir=str(tmp_path / "wf"))
    assert await svc.list_blobs("nope", "", limit=10) == []


async def test_files_read_blob_missing_raises(tmp_path):
    svc = LocalFilesService(working_dir=str(tmp_path / "wf"))
    with pytest.raises(FileNotFoundError):
        await svc.read_blob("c", "x.txt")


# ---------------------------------------------------------------------------
# LocalSecretsService
# ---------------------------------------------------------------------------


def test_load_dotenv_parses_quotes_and_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nPLAIN=value\nQUOTED=\"quoted value\"\nSINGLE='single'\nnoequalsline\n\n",
        encoding="utf-8",
    )
    parsed = _load_dotenv(str(env))
    assert parsed == {"PLAIN": "value", "QUOTED": "quoted value", "SINGLE": "single"}


def test_load_dotenv_missing_returns_empty(tmp_path):
    assert _load_dotenv(str(tmp_path / "absent")) == {}


async def test_secrets_dotenv_source(tmp_path):
    env = tmp_path / ".env"
    env.write_text("API=abc\n", encoding="utf-8")
    svc = LocalSecretsService(source="dotenv", dotenv_path=str(env))
    assert await svc.fetch_secret_value("API") == "abc"
    assert svc.get_all_keys() == ["API"]


async def test_secrets_inline_source():
    svc = LocalSecretsService(source="inline", inline={"X": "1"})
    assert await svc.fetch_secret_value("X") == "1"


async def test_secrets_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("FROM_ENV", "envval")
    svc = LocalSecretsService(source="inline", inline={})
    assert await svc.fetch_secret_value("FROM_ENV") == "envval"
    assert await svc.fetch_secret_value("UNSET_SECRET_XYZ") is None


# ---------------------------------------------------------------------------
# LocalLoggingService
# ---------------------------------------------------------------------------


async def test_logging_write_and_read_by_category():
    svc = LocalLoggingService(level="DEBUG", fmt="console")
    await svc.write("info", "general")
    await svc.write("info", "provider call", metadata={"log_category": "provider"})
    extension = await svc.read(category="extension")
    provider = await svc.read(category="provider")
    assert any(e["message"] == "general" for e in extension)
    assert any(e["message"] == "provider call" for e in provider)


async def test_logging_json_format_path():
    svc = LocalLoggingService(level="INFO", fmt="json")
    await svc.write("warning", "json msg", metadata={"k": "v"})
    entries = await svc.read(category="extension")
    assert any(e["message"] == "json msg" for e in entries)


async def test_logging_conversation_logs():
    svc = LocalLoggingService()
    svc.add_conversation_log({"role": "agent", "text": "hi"})
    convo = await svc.get_conversation_logs()
    assert convo[-1]["text"] == "hi"
    via_read = await svc.read(category="conversation")
    assert via_read[-1]["text"] == "hi"


# ---------------------------------------------------------------------------
# LocalOrgFilesService
# ---------------------------------------------------------------------------


async def test_org_files_write_read_list(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    # Production {entry, version} shape; file ID at result["entry"]["id"].
    result = await svc.write("report.txt", "content")
    entry = result["entry"]
    assert entry["name"] == "report.txt"
    assert entry["entry_type"] == "file"
    assert entry["size_bytes"] == len(b"content")
    assert result["version"]["version_number"] == 1
    assert await svc.read_text(entry["id"]) == "content"
    assert await svc.read(entry["id"]) == b"content"
    entries = await svc.list()
    assert any(e["name"] == "report.txt" for e in entries)


async def test_org_files_write_text_and_filter_type(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    await svc.write_text("notes.md", "x")
    await svc.create_folder("docs")
    files = await svc.list(entry_type="file")
    folders = await svc.list(entry_type="folder")
    assert [f["name"] for f in files] == ["notes.md"]
    assert [f["name"] for f in folders] == ["docs"]


async def test_org_files_read_missing_raises(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    with pytest.raises(FileNotFoundError):
        await svc.read("ghost")


async def test_org_files_rename_move_delete(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    meta = await svc.write("a.txt", "data")
    renamed = await svc.rename(meta["entry"]["id"], "b.txt")
    assert renamed["name"] == "b.txt"
    await svc.create_folder("sub")
    moved = await svc.move(renamed["id"], "sub")
    assert moved["id"].replace("\\", "/") == "sub/b.txt"
    await svc.delete(moved["id"])
    assert await svc.find("b.txt") is None


async def test_org_files_find_recursive_and_folder_delete(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    await svc.create_folder("nested")
    await svc.write("deep.txt", "z", parent_id="nested")
    found = await svc.find("deep.txt")
    assert (found or {}).get("entry_type") == "file"
    await svc.delete("nested")
    assert await svc.find("deep.txt") is None


async def test_org_files_list_missing_parent_returns_empty(tmp_path):
    svc = LocalOrgFilesService(org_dir=str(tmp_path / "org"))
    assert await svc.list(parent_id="nope") == []


# ---------------------------------------------------------------------------
# LocalInfraStubs
