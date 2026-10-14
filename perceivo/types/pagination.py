"""Define ``Page[T]``, the return shape for every list-returning call.

A plural verb returns a page; a singular verb returns one thing. ``total``
tells a caller whether it holds every result or only a prefix.

``Page`` is transport-agnostic. It holds a ``_fetch`` callback rather than a
client, so it can be constructed by the cloud plane, the local plane, or a
test without any of them knowing about each other.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Generic, TypeVar

__all__ = ["Page"]

T = TypeVar("T")

#: Given a 1-based page number, return that page. ``None`` marks a page that
#: cannot fetch further, such as a local result.
Fetcher = Callable[[int], Awaitable["Page[T]"]]


@dataclass
class Page(Generic[T]):
    """One page of results, plus the means to reach the next.

    ``total`` is the count across *all* pages, not the length of ``data``.
    :meth:`__len__` is intentionally not defined, because ``len(page)`` would be
    ambiguous. Use ``len(page.data)`` or ``page.total``.
    """

    data: list[T]
    page: int = 1
    page_size: int = 0
    total: int = 0
    _fetch: Fetcher[T] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.page_size == 0:
            self.page_size = len(self.data)

    def __iter__(self) -> Iterator[T]:
        """Iterate **the current page only**.

        Sync iteration cannot fetch, so making this walk every page is not an
        option; making it *look* like it does would be worse. :meth:`all` is
        the every-page form and it is async precisely so the cost is visible.
        """
        return iter(self.data)

    @property
    def has_next(self) -> bool:
        if self._fetch is None or not self.data:
            return False
        if self.total:
            return self.page * self.page_size < self.total
        # No total reported: a short page is the only end-of-results signal.
        return len(self.data) >= self.page_size

    async def next(self) -> Page[T] | None:
        """The next page, or ``None`` at the end."""
        if not self.has_next or self._fetch is None:
            return None
        return await self._fetch(self.page + 1)

    async def all(self) -> AsyncIterator[T]:
        """Every item across every page, fetched lazily.

        Guarded against a server that keeps returning the same page: a
        non-advancing page number stops the walk instead of looping forever.
        An unpaginated endpoint that ignores ``page`` is exactly the shape
        that turned a page-walk into an OOM once already.
        """
        current: Page[T] | None = self
        seen_pages: set[int] = set()
        while current is not None:
            if current.page in seen_pages:
                raise RuntimeError(
                    f"pagination did not advance past page {current.page}; "
                    "the endpoint appears to ignore the page parameter"
                )
            seen_pages.add(current.page)
            for item in current.data:
                yield item
            current = await current.next()

    @classmethod
    def of(cls, items: list[T]) -> Page[T]:
        """A single complete page. For local results and tests."""
        return cls(data=items, page=1, page_size=len(items), total=len(items))
