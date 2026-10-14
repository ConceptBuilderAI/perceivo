"""The ``managed_service`` category grid — resolution of a grid that carries no verbs.

``managed_service.list()`` and ``<category>.status/.backups/.backup()`` are not
part of the SDK: each would forward to ``ExtensionManager`` methods that are
defined in no runtime. The measurement and the tests that pin it are in
``test_managed_service_withdrawal.py``; this file asserts nothing about them
beyond their absence.

What is here is the grid itself, and it exists on purpose. **A grid with no verbs is
not a dead grid**: the name conformance check's live reader probes ``__getattr__`` to
learn the per-object verb set, and that probe is how
``managed_service.<object>.{connect,start,stop}`` are reported absent and excused.
Deleting it would silence three findings and turn three declared debts into blocking
``STALE_EXCEPTION``s.

Four properties carry the module, and each guards against a defect that is easy to
reintroduce:

1. **The object segment is not callable.** Asserted for the
   hand-written objects *and* for a synthesised one, because the synthesised
   ones are the easy place to lose it.
2. **Resolution is derived, not enumerated.** No list of categories
   exists anywhere in the package, so there is nothing to drift — and the cost
   of that, a typo resolving, is asserted rather than left in a docstring.
3. **Every spelling resolves to the same object, and the one that cannot is
   broken identically every time.** ``import perceivo.managed_service.cache``
   fails always rather than depending on what ran first.
4. **``dir()`` tells the truth and nothing else.** A package ``dir()`` that reports
   its own imports makes names such as ``sys.stdout`` look like SDK surface.

**No credential appears in this file**, and no test here reaches a backend.
"""

from __future__ import annotations

import inspect
import sys
import types as _types

import pytest

import perceivo
from perceivo.managed_service import _catalog
from perceivo.types.pagination import Page

_MS = perceivo.managed_service

#: A name that is not in ``managed_service_catalog`` and never will be. Used to
#: force fresh synthesis without colliding with a category a later test wants.
_SYNTHETIC = "cache"


# ---------------------------------------------------------------------------
# The plane surface
# ---------------------------------------------------------------------------


def test_plane_exports_exactly_the_declared_members():
    """``__all__`` is what shipped, not what the design declares.

    ``list`` was here and is withdrawn — see
    ``test_managed_service_withdrawal.py`` for why, and
    ``tests/conformance/EXCEPTIONS.json`` for the debt it left behind.
    """
    assert _MS.__all__ == ["ai", "database"]


def test_categories_are_absent_from_all():
    """The catalog is live, so ``__all__`` may not snapshot it.

    Enumerating today's seven categories here would churn the surface lock on
    every catalog change and would be wrong the first time one was added.
    """
    for category in ("cache", "messaging", "analytics", "storage", "compute", "other"):
        assert category not in _MS.__all__


def test_dir_reports_all_plus_resolved_categories_only():
    """No ``sys``, no ``types``: the package's own imports are not surface."""
    listed = dir(_MS)
    assert set(listed) >= set(_MS.__all__)
    assert listed == sorted(listed)
    for leaked in ("sys", "types", "Page", "annotations", "Any", "builtins"):
        assert leaked not in listed


def test_dir_grows_when_a_category_is_resolved():
    """``dir()`` is honest about this process, not authoritative about the catalog."""
    fresh = "eventhub"
    assert fresh not in dir(_MS)
    getattr(_MS, fresh)
    try:
        assert fresh in dir(_MS)
        assert fresh in _catalog.resolved()
    finally:
        delattr(_MS, fresh)
        _catalog._RESOLVED.discard(fresh)


# ---------------------------------------------------------------------------
# The object segment must not be callable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("segment", ["ai", "database", _SYNTHETIC, "messaging"])
def test_object_segment_is_not_callable(segment):
    """The object segment is not callable, hand-written and synthesised alike.

    A callable object segment means ``managed_service.database(...)`` resolves
    to *something* when a call site forgets the verb. Modules cannot be called,
    so the mistake fails at the call rather than three frames later.
    """
    handle = getattr(_MS, segment)
    assert not callable(handle)
    with pytest.raises(TypeError):
        handle()  # type: ignore[operator]


def test_synthesised_handles_are_modules_like_the_hand_written_ones():
    """Same ``type()`` for every category, so a reader can predict one from another."""
    assert isinstance(_MS.database, _types.ModuleType)
    assert isinstance(getattr(_MS, _SYNTHETIC), _types.ModuleType)
    assert type(_MS.database) is type(getattr(_MS, _SYNTHETIC))


# ---------------------------------------------------------------------------
# Resolution: same object, every spelling, every time
# ---------------------------------------------------------------------------


def test_resolution_is_cached_on_the_package():
    """Two accesses give the same handle — not two modules with equal contents."""
    assert getattr(_MS, _SYNTHETIC) is getattr(_MS, _SYNTHETIC)


def test_from_import_spelling_resolves():
    """``from perceivo.managed_service import analytics`` goes through ``__getattr__``."""
    from perceivo.managed_service import analytics

    assert analytics.__name__ == "perceivo.managed_service.analytics"
    assert analytics is _MS.analytics


def test_import_module_spelling_fails_the_same_way_every_time():
    """The one spelling that does not work, and the reason it is left broken.

    ``import perceivo.managed_service.cache`` looks for a *file*. Registering
    the synthesised handle in ``sys.modules`` would make this succeed only when
    something earlier in the process had touched the attribute — an
    order-dependent surface, which the surface lock cannot record
    reliably. Failing identically every time
    is the property worth keeping, so it is asserted rather than tolerated.
    """
    getattr(_MS, _SYNTHETIC)  # touch the attribute first: still must fail
    sys.modules.pop(f"perceivo.managed_service.{_SYNTHETIC}", None)
    with pytest.raises(ModuleNotFoundError):
        __import__(f"perceivo.managed_service.{_SYNTHETIC}")


def test_a_synthesised_handle_is_shaped_like_a_module_and_carries_no_verbs():
    """The empty ``__all__`` is the withdrawal, visible from the handle itself.

    ``backup``, ``backups`` and ``status`` are absent because each would
    forward to ``ExtensionManager`` methods with 0 definitions in every runtime;
    the handle keeps its identity, its docstring and its uncallability, and
    simply has nothing on it.
    """
    cache = getattr(_MS, _SYNTHETIC)
    assert cache.__all__ == []
    assert cache.__package__ == "perceivo.managed_service"
    assert "carries no verbs" in (cache.__doc__ or "")
    assert "§" not in (cache.__doc__ or "")
    assert _SYNTHETIC in (cache.__doc__ or ""), "the doc names the category it is"


# ---------------------------------------------------------------------------
# The category-name guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "_private",
        "__wrapped__",
        "_ipython_canary_method_should_not_exist_",
        "NotACategory",
        "camelCase",
        "9starts_with_digit",
        "has-a-dash",
        "has space",
        "ünicode",
        "",
    ],
)
def test_names_that_are_not_category_shaped_raise_attribute_error(name):
    """``__getattr__`` must keep ``hasattr`` honest.

    The import system, pytest, ``inspect`` and ``copy`` all probe a package with
    dunder and private names. Answering ``__wrapped__`` with a synthesised module
    hands the interpreter a module where it expected a function, and the failure
    surfaces somewhere else entirely.
    """
    with pytest.raises(AttributeError):
        getattr(_MS, name)


@pytest.mark.parametrize("name", ["__path__", "__all__", "__name__", "__doc__"])
def test_real_package_dunders_are_untouched_by_the_grid(name):
    """``__getattr__`` runs only for names that are *not* already bound.

    Asserted because the grid answers almost anything: it would be easy to read
    the guard as the only thing standing between ``__path__`` and a synthesised
    module, and it is not — the attribute exists, so the hook never fires.
    """
    assert not isinstance(getattr(_MS, name), _types.ModuleType)


def test_a_mistyped_category_still_resolves_and_that_is_documented():
    """The stated cost of deriving rather than enumerating (see ``_catalog``).

    ``managed_service.databse`` produces a handle. Validating the segment would
    mean either shipping a snapshot of the catalog, which only a release could update —
    or making an I/O call on attribute access, offline, with no execution
    context. So the typo resolves, and the mistake surfaces at the *next*
    attribute rather than at the wire, which is asserted here so the trade-off
    is visible in the suite rather than only in a docstring.
    """
    typo = _MS.databse
    try:
        assert isinstance(typo, _types.ModuleType)
        assert typo.__all__ == []
        with pytest.raises(AttributeError):
            # the real `database` has `connect`; the typo has nothing
            getattr(typo, "connect")  # noqa: B009 — the raise IS the assertion
    finally:
        delattr(_MS, "databse")
        _catalog._RESOLVED.discard("databse")


# ---------------------------------------------------------------------------
# What the plane still ships
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn",
    [
        perceivo.managed_service.database.connect,
        perceivo.managed_service.ai.invoke,
        perceivo.managed_service.ai.models,
    ],
    ids=["database.connect", "ai.invoke", "ai.models"],
)
def test_every_shipping_verb_is_async(fn):
    """The SDK is async throughout.

    These three plus ``DatabaseClient.query`` are the whole of what
    ``managed_service`` builds, and each has a measured backend —
    ``execute_database_query``, ``invoke_ai_foundry`` and ``embed_ai_foundry``
    each have a definition in the platform orchestration service, which defines
    none of the five managed-service methods.
    """
    assert inspect.iscoroutinefunction(fn)


def test_the_verbs_that_do_not_ship_are_absent():
    """The verbs that are not delivered, asserted from the other side.

    All six category verbs are absent and all six are declared debt.
    ``connect`` exists on ``database`` only, because the design *removes*
    ``get_connection`` — every use of it is a credential in a variable — so a
    generic ``connect`` would have to be built rather than forwarded.
    """
    cache = getattr(_MS, _SYNTHETIC)
    for absent in ("status", "backups", "backup", "start", "stop", "connect"):
        assert not hasattr(cache, absent), absent
    assert hasattr(_MS.database, "connect")
    assert not hasattr(_MS.database, "start")
    assert not hasattr(_MS.database, "stop")


def test_get_connection_is_not_reachable_from_the_managed_service_plane():
    """No path from the plane to a credential-returning call.

    The design excludes ``get_connection`` outright, and its absence must read as an
    absence rather than as a rename. The platform method such a call would
    forward to, ``get_managed_service_connection``, measures 0 definitions and
    0 mentions in every runtime.
    """
    for module in (_MS, _MS.database, _catalog, getattr(_MS, _SYNTHETIC)):
        assert not hasattr(module, "get_connection")


# ---------------------------------------------------------------------------
# ai.models() — the page container
# ---------------------------------------------------------------------------


async def test_ai_models_returns_a_page(monkeypatch):
    """The design declares ``Page[ModelInfo]``, not a bare list.

    The container is a ``Page``. The *element* type stays ``dict`` and is
    carried as a named entry in ``tests/conformance/EXCEPTIONS.json`` — building
    a ``ModelInfo`` would mean deriving a wire contract from whichever keys the
    platform happens to return, when defining that contract belongs to the API
    specification.
    """
    models = [{"id": "gpt-4o"}, {"id": "text-embedding-3-small"}]

    async def _list_models():
        return models

    monkeypatch.setattr(perceivo.managed_service.ai, "_list_models", _list_models)
    monkeypatch.setattr(
        perceivo.managed_service.ai._ExecutionContext, "is_active", staticmethod(lambda: True)
    )

    page = await perceivo.managed_service.ai.models()
    assert isinstance(page, Page)
    assert page.data == models
    assert page.total == page.page_size == 2
    assert page.has_next is False
