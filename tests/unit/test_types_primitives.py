"""``Page`` and the vocabulary enums -- the two primitives everything else uses.

These are greenfield: nothing in the corpus calls them yet, so nothing here is
a regression test. They are pinning *design decisions* that are easy to undo by
accident -- ``StrEnum`` rather than ``Enum``, no ``__len__`` on ``Page``, a
page-walk that refuses to loop -- each of which has a specific failure it exists
to prevent.
"""

from __future__ import annotations

import enum

import pytest

import perceivo
from perceivo.files import StorageContainer
from perceivo.log import LogType
from perceivo.reviews import ReviewSubject
from perceivo.types.enums import AIService, Level, Runner, Severity
from perceivo.types.pagination import Page

ALL_ENUMS = [AIService, LogType, StorageContainer, Level, Runner, ReviewSubject, Severity]


class TestTheEnumsAcceptPlainStrings:
    """The property that lets these land as pure additions."""

    def test_every_enum_is_a_str_enum(self) -> None:
        # A plain Enum would make every existing call site that passes a bare
        # string a breaking change. That is the whole argument for StrEnum, so
        # it is asserted rather than assumed.
        for cls in ALL_ENUMS:
            assert issubclass(cls, str), cls.__name__
            assert issubclass(cls, enum.Enum), cls.__name__

    def test_a_member_compares_equal_to_its_value(self) -> None:
        assert LogType.PROVIDER == "provider"
        assert StorageContainer.INTELLIGENCE == "intelligence"
        assert AIService.DOCUMENT_INTEL == "document_intelligence"

    def test_a_member_is_usable_where_a_string_is(self) -> None:
        # The realistic call shape: a dict keyed by literal strings, indexed
        # with an enum member by a caller who adopted the enum.
        handlers = {"chat": "chat-handler", "embedding": "embed-handler"}
        assert handlers[AIService.CHAT] == "chat-handler"
        assert f"log_type={LogType.ERROR}" == "log_type=error"

    def test_no_two_members_of_an_enum_share_a_value(self) -> None:
        # StrEnum silently aliases duplicates, so a copy-paste slip would make
        # two names the same member and the second would never be reachable.
        for cls in ALL_ENUMS:
            values = [m.value for m in cls]
            assert len(values) == len(set(values)), cls.__name__


class TestTheWritableChannelsAreTheDocumentedOnes:
    """``writable`` keeps the one exception in a single place."""

    def test_only_the_extension_channel_is_writable(self) -> None:
        writable = {c for c in LogType if c.writable}
        assert writable == {LogType.EXTENSION}

    def test_no_container_is_writable_and_none_carries_the_property(self) -> None:
        # The org store is `perceivo.files`, which takes no container at all,
        # and the remaining three containers sit behind `perceivo.files.platform`,
        # which has no write verb. A `writable` flag on a namespace that cannot
        # write is a claim about a function that does not exist, so the flag
        # goes with the member. Pinned in both directions: no member answers to
        # `writable`, and `ORG` is not reachable by name.
        assert not hasattr(StorageContainer, "ORG")
        assert not any(hasattr(c, "writable") for c in StorageContainer)
        assert {c.value for c in StorageContainer} == {
            "orchestrations",
            "intelligence",
            "infrastructure",
        }


class TestTheEnumsLiveOnTheirPlanes:
    def test_each_enum_is_exported_by_its_plane_and_not_the_root(self) -> None:
        homes = {
            AIService: perceivo.types,
            Level: perceivo.types,
            Runner: perceivo.types,
            Severity: perceivo.types,
            LogType: perceivo.log,
            StorageContainer: perceivo.files,
            ReviewSubject: perceivo.reviews,
        }
        assert set(homes) == set(ALL_ENUMS)
        for cls, plane in homes.items():
            assert getattr(plane, cls.__name__) is cls, cls.__name__
            assert cls.__name__ in plane.__all__, cls.__name__
            assert not hasattr(perceivo, cls.__name__), cls.__name__

    def test_the_retired_enums_are_gone(self) -> None:
        import perceivo.types.enums as enums

        for gone in ("Channel", "Container", "Subject", "Metric"):
            assert not hasattr(enums, gone), gone
            assert not hasattr(perceivo.types, gone), gone

    def test_review_subject_is_the_three_supported_kinds(self) -> None:
        assert {s.value for s in ReviewSubject} == {"component", "orchestration", "capability"}

    def test_runner_covers_the_five_runtimes_plus_evaluation(self) -> None:
        # Internal imports are blocked in five runtimes; the Runner enum is
        # the vocabulary for the same set. If they drift, a run record can name
        # a runtime the platform does not have.
        assert {r.value for r in Runner} == {
            "orchestration",
            "capability",
            "feature",
            "training",
            "inference",
            "evaluation",
        }


class TestPageAnswersOneQuestionAtATime:
    def test_page_has_no_len(self) -> None:
        # `len(page)` could mean "items on this page" or "items in total", and
        # would be quietly wrong about one of them. Removing the ambiguity is
        # the design decision; this is what stops someone adding it back.
        page = Page.of([1, 2, 3])
        with pytest.raises(TypeError):
            len(page)  # type: ignore[arg-type]

    def test_iteration_covers_the_current_page_only(self) -> None:
        page = Page(data=[1, 2], page=1, page_size=2, total=10)
        assert list(page) == [1, 2]

    def test_of_builds_a_single_complete_page(self) -> None:
        page = Page.of(["a", "b"])
        assert page.data == ["a", "b"]
        assert page.page == 1
        assert page.page_size == 2
        assert page.total == 2
        assert page.has_next is False

    def test_page_size_defaults_to_the_data_length(self) -> None:
        assert Page(data=[1, 2, 3]).page_size == 3

    def test_an_empty_page_has_no_next(self) -> None:
        async def _fetch(_n: int) -> Page[int]:  # pragma: no cover - never called
            raise AssertionError("must not fetch past an empty page")

        assert Page(data=[], page_size=10, total=0, _fetch=_fetch).has_next is False


class TestThePageWalk:
    @staticmethod
    def _paged(pages: list[list[int]], total: int | None = None) -> Page[int]:
        """A fake endpoint over ``pages``, 1-indexed like the real one."""
        size = max((len(p) for p in pages), default=0)
        reported = len(pages) * size if total is None else total

        async def fetch(n: int) -> Page[int]:
            return Page(
                data=pages[n - 1],
                page=n,
                page_size=size,
                total=reported,
                _fetch=fetch,
            )

        return Page(data=pages[0], page=1, page_size=size, total=reported, _fetch=fetch)

    @pytest.mark.asyncio
    async def test_all_walks_every_page(self) -> None:
        page = self._paged([[1, 2], [3, 4], [5]], total=5)
        assert [item async for item in page.all()] == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_next_returns_none_at_the_end(self) -> None:
        page = self._paged([[1, 2], [3]], total=3)
        second = await page.next()
        assert second is not None
        assert second.data == [3]
        assert await second.next() is None

    def test_a_page_with_no_fetcher_never_claims_a_next(self) -> None:
        # Local results and test fixtures build these. Claiming has_next with
        # nothing to call would strand the caller.
        assert Page(data=[1], page_size=1, total=99).has_next is False

    @pytest.mark.asyncio
    async def test_a_non_advancing_endpoint_stops_the_walk(self) -> None:
        # The exact shape behind the components-list OOM: an endpoint that
        # ignores `page` and returns the same rows forever. Without this guard
        # `all()` is an infinite generator that fills memory.
        async def stuck(_n: int) -> Page[int]:
            return Page(data=[1, 2], page=1, page_size=2, total=99, _fetch=stuck)

        page = Page(data=[1, 2], page=1, page_size=2, total=99, _fetch=stuck)
        with pytest.raises(RuntimeError, match="did not advance"):
            _ = [item async for item in page.all()]

    @pytest.mark.asyncio
    async def test_a_short_page_ends_the_walk_when_no_total_is_reported(self) -> None:
        async def fetch(n: int) -> Page[int]:
            return Page(data=[9], page=n, page_size=2, total=0, _fetch=fetch)

        page = Page(data=[1, 2], page=1, page_size=2, total=0, _fetch=fetch)
        assert [item async for item in page.all()] == [1, 2, 9]

    def test_the_fetcher_is_not_part_of_equality_or_repr(self) -> None:
        # Two pages holding the same rows are the same page; a closure identity
        # is not a difference a caller can act on, and it would make repr()
        # leak an object address into any log line that prints one.
        async def fetch(_n: int) -> Page[int]:  # pragma: no cover - never called
            raise AssertionError

        left = Page(data=[1], page=1, page_size=1, total=1)
        right = Page(data=[1], page=1, page_size=1, total=1, _fetch=fetch)
        assert left == right
        assert "fetch" not in repr(right)
        assert "0x" not in repr(right)
