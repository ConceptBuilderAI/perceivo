"""The ``perceivo.files`` plane, and the decision behind its shape.

``perceivo.files`` is the org store: ``write_text``, ``read_text``, ``list``,
``delete`` and ``find``, with ``read``/``write`` addressing an entry by id. The
run's working container, addressed by path, is under
:mod:`perceivo.files.working`. There is no ``perceivo.storage`` package and no
alias for it.

The tests below pin *both* halves, because an org-store plane that quietly took
the working-container capability with it would look identical to one that did
not.

Behaviour is exercised through a real ``LocalExtensionManager`` (the
``local_context`` fixture), not a mock, so these are the actual code paths a
component runs.
"""

from __future__ import annotations

import importlib
import inspect
import types

import pytest

import perceivo


class TestTheFiveBuiltMembersWork:
    """The five members, exercised rather than asserted.

    A compatibility inventory can drop a member from its remaining-work list
    the moment the name *resolves*, which is a weaker fact than the member
    working. These are the five supported members, called for real.
    """

    async def test_write_text_then_read_text_round_trips(self, local_context):
        result = await perceivo.files.write_text("report.txt", "hello")
        entry = result["entry"]
        assert entry["name"] == "report.txt"
        assert result["version"]["version_number"] == 1
        assert await perceivo.files.read_text(entry["id"]) == "hello"

    async def test_list_returns_what_was_written(self, local_context):
        await perceivo.files.write_text("listed.txt", "x")
        entries = await perceivo.files.list()
        assert any(e.name == "listed.txt" for e in entries)

    async def test_list_accepts_the_kwargs_the_corpus_already_passes(self, local_context):
        """The signature is load-bearing, not incidental.

        Five corpus sites call ``perceivo.files.list`` today and raise
        ``AttributeError``; they pass ``storage.list``'s exact keywords. That
        is the accepted repair collision in
        ``build_rename_map.ACCEPTED_REPAIR_COLLISIONS`` -- and it is only
        accepted on the basis that those keywords bind here.
        """
        assert list(inspect.signature(perceivo.files.list).parameters) == [
            "parent_id",
            "space_id",
            "entry_type",
        ]
        assert (
            await perceivo.files.list(parent_id=None, space_id=None, entry_type="file") is not None
        )

    async def test_find_returns_the_entry_or_none(self, local_context):
        await perceivo.files.write_text("findme.txt", "y")
        found = await perceivo.files.find("findme.txt")
        assert found is not None
        assert found.name == "findme.txt"
        assert await perceivo.files.find("nope.txt") is None

    async def test_find_returns_none_not_a_page(self, local_context):
        """The design asks for ``Page[FileEntry]``; this returns an entry or None.

        The divergence is argued in ``perceivo/files/_org.py``. The reason it
        cannot be closed silently is testable: all three corpus sites are
        ``entry = await find(x); if entry is not None:``, and an empty page is
        truthy, so the guard would stop working while every call still
        succeeded.
        """
        assert await perceivo.files.find("absent.txt") is None

    async def test_delete_removes_the_entry(self, local_context):
        meta = await perceivo.files.write_text("gone.txt", "z")
        await perceivo.files.delete(meta["entry"]["id"])
        entries = await perceivo.files.list()
        assert not any(e.name == "gone.txt" for e in entries)


class TestTheDeliberateRebaseline:
    """``files.read``/``write`` changed meaning. Both meanings still exist."""

    async def test_read_and_write_address_the_org_store_by_entry_id(self, local_context):
        result = await perceivo.files.write("data.bin", b"\x01\x02")
        assert result["entry"]["size_bytes"] == 2
        assert await perceivo.files.read(result["entry"]["id"]) == b"\x01\x02"

    async def test_the_working_container_keeps_its_path_addressed_contract(self, local_context):
        """The capability the org-store plane could delete by accident.

        ``FilesService.read`` resolves to ``FILETYPE.WORKING`` on the platform
        and to a directory locally
        (``perceivo/local/adapters/files.py:19-34``). Handing its two names to
        the org store without keeping it reachable would be a silent capability
        deletion inside a change that is nominally a rename.
        """
        await perceivo.files.working.write("scratch.txt", "intermediate")
        assert await perceivo.files.working.read("scratch.txt") == "intermediate"

    async def test_the_two_planes_are_separate_stores(self, local_context):
        """Not just two names -- two containers.

        Written to the working container, an entry must not be findable in the
        org store. If it were, the "re-baseline" would be cosmetic and the
        distinction the whole decision rests on would not exist.
        """
        await perceivo.files.working.write("only_working.txt", "data")
        assert await perceivo.files.find("only_working.txt") is None

    def test_the_org_read_takes_an_entry_id_and_returns_bytes(self):
        sig = inspect.signature(perceivo.files.read)
        assert list(sig.parameters) == ["entry_id"]
        assert sig.return_annotation is bytes

    def test_the_working_read_takes_a_path_and_returns_str(self):
        sig = inspect.signature(perceivo.files.working.read)
        assert list(sig.parameters) == ["file_path"]
        assert sig.return_annotation is str


class TestTheWorkingHandleIsNotCallable:
    """The object segment must not be callable."""

    def test_working_is_a_module(self):
        assert isinstance(perceivo.files.working, types.ModuleType)

    def test_calling_the_handle_fails_immediately(self):
        with pytest.raises(TypeError):
            perceivo.files.working("scratch.txt")  # type: ignore[operator]


class TestContentTypeIsNotPartOfTheWriteTextContract:
    """The decision, and the reason it is a decision rather than an omission.

    One corpus site passes it --
    ``lexograph_corpus/lexograph-position-generator.json`` calls
    ``write_text(path, json.dumps(...), content_type='application/json')`` --
    and it raises ``TypeError`` today. Accepting the keyword would make that
    call start working, and every asset must produce the same run record through
    the public plane as through the implementation behind it, so a red-to-green
    change fails the preservation check exactly as a green-to-red one does. It
    is also a misspelling rather than a gap: the MIME channel on this plane is
    ``mime_type``, and it is on :func:`write`. So the corpus site is the one to
    fix, and the signature holds.
    """

    def test_write_text_has_no_content_type_parameter(self):
        assert "content_type" not in inspect.signature(perceivo.files.write_text).parameters

    async def test_passing_content_type_still_raises(self, local_context):
        with pytest.raises(TypeError):
            await perceivo.files.write_text("x.json", "{}", content_type="application/json")

    def test_the_mime_channel_exists_under_its_real_name(self):
        assert "mime_type" in inspect.signature(perceivo.files.write).parameters


class TestTheStorageShimIsGone:
    """``perceivo.storage`` does not exist, and there is no alias for it.

    The assertion has to be a *positive* one. An absent test is not evidence
    of anything -- a package that still imports would produce exactly that.
    The import is attempted and the failure is required.

    ``build_rename_map.py`` cannot resolve the eight ``perceivo.storage``
    symbols from this tree. Its dead-rule guard aborts rather than emitting a
    map with eight rows flipped to ``ABSENT``, so the committed map is not
    regenerated from it.
    """

    def test_the_storage_package_does_not_import(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("perceivo.storage")

    def test_it_is_not_reachable_as_an_attribute_either(self):
        """Two spellings, two failure modes.

        ``perceivo/__init__.py`` carries a ``__getattr__`` for lazily loaded
        planes, so a name that is not imported eagerly can still answer to
        attribute access. A removal that only closed the
        ``import`` spelling would leave every ``perceivo.storage.read`` call
        site working.
        """
        assert not hasattr(perceivo, "storage")

    def test_org_files_does_not_import_either(self):
        """The same removal, one plane over.

        There is no ``org_files`` spelling of this plane, and the property
        worth pinning is that the module is *unreachable* rather than merely
        absent from ``__all__``.

        Both spellings have to be closed, because they fail differently: an
        empty leftover directory on ``sys.path`` stays importable as a PEP 420
        namespace package while every ``__all__``-shaped check reports clean.
        The parametrised sweep in
        ``tests/conformance/test_removed_planes_are_unimportable.py`` covers all
        fifteen removals that way; this row stays here so the failure also lands
        beside the plane that absorbed it.
        """
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("perceivo.org_files")
        assert not hasattr(perceivo, "org_files")

    def test_the_resolver_internals_are_not_republished_on_the_files_plane(self):
        """They are not part of the public surface, and no seed asset uses them.

        This asserts they are not on the plane that holds the org-store
        members.
        """
        for name in ("StorageZone", "ZoneLocation", "OrgContainerResolver"):
            assert not hasattr(perceivo.files, name)
