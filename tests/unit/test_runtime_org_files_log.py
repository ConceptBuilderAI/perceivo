"""Unit tests for runtime modules: perceivo.files, perceivo.log.

These exercise the public SDK functions through a real LocalExtensionManager
pushed onto the execution-context stack (the ``local_context`` fixture).
"""

from __future__ import annotations

import pytest

import perceivo
import perceivo.files as org_files
import perceivo.files._org as org_files_impl
import perceivo.log as log
import perceivo.log._logger as log_impl

# ``rename`` and the whole log read path are off their planes — `files` declares
# ten verbs without `rename`, and `log` declares a single `query` in place of
# `read`, `log` and `get_conversation_logs`, which is deferred. The modules
# that define them are what these tests exercise.
# ``test_the_removed_spellings_are_off_their_planes`` below is the other half:
# it pins that the *planes* do not answer to them.

# ---------------------------------------------------------------------------
# perceivo.files (public async API → LocalOrgFilesService)
# ---------------------------------------------------------------------------


async def test_org_files_write_read_list_roundtrip(local_context):
    # write/write_text return the production {entry, version} shape; the file
    # ID lives at result["entry"]["id"].
    result = await org_files.write_text("report.txt", "hello")
    entry = result["entry"]
    assert entry["name"] == "report.txt"
    assert entry["entry_type"] == "file"
    assert result["version"]["version_number"] == 1
    assert await org_files.read_text(entry["id"]) == "hello"
    assert await org_files.read(entry["id"]) == b"hello"
    entries = await org_files.list()
    assert any(e.name == "report.txt" for e in entries)


async def test_org_files_write_bytes(local_context):
    result = await org_files.write("data.bin", b"\x01\x02")
    assert result["entry"]["size_bytes"] == 2
    assert result["version"]["size_bytes"] == 2


async def test_org_files_mkdir_rename_move_delete(local_context):
    f = await org_files.mkdir("inbox")
    assert f["name"] == "inbox"
    meta = await org_files.write_text("note.txt", "x")
    renamed = await org_files_impl.rename(meta["entry"]["id"], "note2.txt")
    assert renamed["name"] == "note2.txt"
    moved = await org_files.move(renamed["id"], "inbox")
    assert "inbox" in moved["id"]
    await org_files.delete(moved["id"])


async def test_org_files_find_present_and_absent(local_context):
    await org_files.write_text("findme.txt", "y")
    found = await org_files.find("findme.txt")
    assert found is not None
    assert found.name == "findme.txt"
    assert await org_files.find("nope.txt") is None


async def test_org_files_find_returns_none_outside_context(cleared_context):
    # No ext on the stack -> find() swallows the RuntimeError and returns None.
    assert await org_files.find("anything") is None


# ---------------------------------------------------------------------------
# perceivo.log (public API → LocalLoggingService)
# ---------------------------------------------------------------------------


async def test_log_write_and_query(local_context):
    await log.write("async entry", "info")
    page = await log.query(log.LogType.EXTENSION)
    assert any(e.message == "async entry" for e in page)
    assert all(e.log_type is log.LogType.EXTENSION for e in page)


async def test_log_query_reads_the_conversation(local_context):
    local_context.logging.add_conversation_log({"role": "agent", "text": "hi"})
    page = await log.query(log.LogType.CONVERSATION)
    assert page.data[-1].message == "hi"
    assert page.data[-1].role == "agent"
    assert page.data[-1].raw == {"role": "agent", "text": "hi"}


async def test_log_query_accepts_the_bare_value(local_context):
    local_context.logging.add_conversation_log({"role": "agent", "text": "hi"})
    page = await log.query("conversation")
    assert [e.message for e in page] == ["hi"]


async def test_log_query_rejects_an_unknown_log_type(local_context):
    with pytest.raises(perceivo.types.ContextError, match="not a LogType value"):
        await log.query("transition")


def test_log_types_are_the_platform_values():
    """The run-log publisher's values."""
    assert {t.value for t in log.LogType} == {
        "provider",
        "conversation",
        "conversation_input",
        "conversation_stream",
        "error",
        "extension",
        "snapshot",
        "state_transition",
    }
    assert [t for t in log.LogType if t.writable] == [log.LogType.EXTENSION]


def test_log_sync_info_no_running_loop(local_context):
    # Called from sync context: info()/warning()/etc. drive their own loop.
    log.info("sync info", k="v")
    log.debug("sync debug")
    log.warning("sync warning")
    log.error("sync error")
    # No exception == success; entries are stored on the logging service.
    assert any(e["message"] == "sync info" for e in local_context.logging._log_entries)


async def test_log_query_platform_serves_the_conversation_only(stub_ext):
    """The platform LoggingService has no ``read``: CONVERSATION only."""

    class _PlatformLogging:
        async def write(self, *a, **k):
            return None

        async def get_conversation_logs(self, limit, chain=False):
            return [{"role": "agent", "text": "x"}]

    stub_ext.logging = _PlatformLogging()
    page = await log.query(log.LogType.CONVERSATION)
    assert [e.message for e in page] == ["x"]
    with pytest.raises(perceivo.types.ContextError, match="not readable in this runtime"):
        await log.query(log.LogType.PROVIDER)


# ---------------------------------------------------------------------------
# The plane surfaces
# ---------------------------------------------------------------------------


def test_the_removed_spellings_are_off_their_planes():
    """The other half of the repointing above.

    Each name below still works — the tests in this file call every one of them
    through the module that defines it — so the only thing that can regress is
    the *plane* quietly regaining it. That is what this pins.
    """
    assert not hasattr(org_files, "rename"), "files.rename is back on the plane"
    for gone in ("read", "log", "get_conversation_logs"):
        assert not hasattr(log, gone), f"log.{gone} is back on the plane"
        assert not hasattr(log_impl, gone), f"the legacy log.{gone} is back"
    for gone in ("PROVIDER", "TRANSITION", "ERROR", "CONVERSATION", "SNAPSHOT", "EXTENSION"):
        assert not hasattr(log, gone), f"log.{gone} is back on the plane"
        assert not hasattr(log_impl, gone), f"the legacy category {gone} is back"

    # Positive control, without which a module that failed to import would pass
    # every assertion above by being empty.
    assert sorted(log.__all__) == [
        "LogEntry",
        "LogType",
        "conversation",
        "debug",
        "error",
        "info",
        "query",
        "warning",
        "write",
    ]
    assert "rename" not in org_files.__all__
    assert callable(org_files.move)
    assert callable(log.write)

    # And the implementations really are still there, at their own modules.
    assert callable(org_files_impl.rename)
