"""The in-cluster service grid, derived from the backend.

A hand-written ``__all__`` is correct on the day it is typed and has no way
to stay correct. The package derives its service set from
:mod:`perceivo.cluster._backend`'s export contract, and this file holds the
derivation down.

The properties, and what each one is protecting:

1. **The set is derived, not listed.** Asserted by adding an accessor to the
   backend and showing the new service appears with no edit to the package,
   because a new registration must work without an SDK release.
   Asserting only ``SERVICES == {milvus, redis, elasticsearch}`` would restate
   the implementation and pass equally well against a transcription.
2. **The grid is closed, and that is the deviation from ``_catalog``.** There
   the object segment is not on the wire, so any identifier can be served.
   Here the service name **is** the backend selector — three accessors, three
   ``<svc>_status`` field sets, three client classes — so a name outside the
   derived set raises rather than producing a handle whose ``connect`` forwards
   to nothing. A verb built against a route that does not exist is worse than
   an absent verb, because the checks then read green.
3. **``connect`` is the same function object, not a wrapper.** ``is``, not a
   round-trip, which makes the preservation claim mechanical: the public plane
   and the backend behind it produce the same run record because they are the
   same function.
4. **The object segment is not callable**, for the synthesised handles as well
   as by convention.
5. **``dir()`` is complete and tells the truth.** A package ``dir()`` that
   reports its own imports makes names such as ``sys.stdout`` look like SDK
   surface.

**No credential appears in this file.** ``connect`` is never called: every test
here reads the grid's shape, and the one function that would open a connection
is asserted by identity, not invoked.
"""

from __future__ import annotations

import importlib
import types
from pathlib import Path

import pytest

import perceivo
from perceivo.cluster import _backend as backend
from perceivo.cluster import _registry

_CLUSTER = perceivo.cluster

_ACCESSORS = sorted(n for n in backend.__all__ if n.startswith("get_"))


# ---------------------------------------------------------------------------
# The set is derived from the backend, not written down
# ---------------------------------------------------------------------------


def test_every_service_is_the_backend_accessor_itself() -> None:
    """``SERVICES[name] is _backend.get_<name>`` — no copy, no wrapper."""
    assert _registry.SERVICES, "the grid resolved to an empty backend"
    for name, accessor in _registry.SERVICES.items():
        assert accessor is getattr(backend, f"get_{name}")


def test_the_grid_holds_exactly_the_backend_accessors() -> None:
    """No service without an accessor; no accessor without a service.

    The drift property. A service the SDK names but the platform cannot reach
    is a verb pointing at nothing; an accessor with no service is a capability
    the plane silently drops.
    """
    assert sorted(_registry.SERVICES) == [n.removeprefix("get_") for n in _ACCESSORS]


def test_a_new_backend_accessor_needs_no_edit_to_this_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newly reachable service works without an SDK release.

    The one test that distinguishes a derivation from a transcription. The
    backend is extended and the module re-executed; a hand-written ``SERVICES``
    would pass every other test in this file and fail this one.

    The backend is restored and the module re-executed a second time, so the
    state the rest of the suite sees is the state it started with — ``reload``
    rebinds names in the *same* module object, so the functions other modules
    already hold pick the restoration up with it.
    """

    async def get_kafka() -> object:  # pragma: no cover - never called
        raise AssertionError("the grid must not call an accessor to derive it")

    try:
        monkeypatch.setattr(backend, "get_kafka", get_kafka, raising=False)
        monkeypatch.setattr(backend, "__all__", [*backend.__all__, "get_kafka"])
        extended = importlib.reload(_registry)
        assert "kafka" in extended.SERVICES
        assert extended.SERVICES["kafka"] is get_kafka
    finally:
        monkeypatch.undo()
        importlib.reload(_registry)

    assert "kafka" not in _registry.SERVICES
    assert sorted(_registry.SERVICES) == [n.removeprefix("get_") for n in _ACCESSORS]


def test_all_is_the_derived_service_set() -> None:
    assert _CLUSTER.__all__ == sorted(_registry.SERVICES)


def test_dir_is_complete_and_reports_nothing_else() -> None:
    """Complete, not merely honest — the one place this beats ``_catalog``.

    There the object set comes from a live catalog the SDK cannot read offline,
    so ``dir()`` can only report what has been asked for so far. Here the set
    is known at import. ``sys`` and ``types`` are asserted absent because a
    package ``dir()`` built from ``globals()`` reports its own imports.
    """
    listed = dir(_CLUSTER)
    assert listed == sorted(_registry.SERVICES)
    assert not {"sys", "types", "SERVICES", "resolve"} & set(listed)


@pytest.mark.parametrize("name", sorted(_registry.SERVICES))
def test_every_listed_service_actually_resolves(name: str) -> None:
    """``__all__`` is a promise: ``from perceivo.cluster import *`` must not fail."""
    assert isinstance(getattr(_CLUSTER, name), types.ModuleType)


# ---------------------------------------------------------------------------
# The handles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_registry.SERVICES))
def test_the_handle_is_an_uncallable_module_declaring_one_verb(name: str) -> None:
    """The object segment must not be callable, and a module cannot be called.

    A call site that forgets ``.connect`` fails immediately and loudly instead
    of resolving to something plausible — ``await perceivo.cluster.milvus()``
    is the spelling an asset that treats the object segment as the function
    carries.
    """
    handle = getattr(_CLUSTER, name)
    assert isinstance(handle, types.ModuleType)
    assert not callable(handle)
    assert handle.__all__ == ["connect"]
    assert handle.__name__ == f"perceivo.cluster.{name}"
    assert handle.__package__ == "perceivo.cluster"
    assert handle.__doc__
    assert f"perceivo.cluster.{name}.connect()" in handle.__doc__
    assert "§" not in handle.__doc__


@pytest.mark.parametrize("name", sorted(_registry.SERVICES))
def test_connect_is_the_backend_accessor_object(name: str) -> None:
    """Preservation by construction: the same function, so behaviour cannot drift."""
    assert getattr(_CLUSTER, name).connect is getattr(backend, f"get_{name}")


@pytest.mark.parametrize("name", sorted(_registry.SERVICES))
def test_the_five_unbuilt_verbs_are_absent_rather_than_stubbed(name: str) -> None:
    """The design declares six verbs per service and the SDK ships one.

    ``AttributeError`` is the honest answer, and the reason differs per verb —
    each was checked against the repository that would have to serve it, and is
    recorded in
    :mod:`perceivo.cluster._registry`. In short: ``status`` needs one public
    method on ``InfraServiceMixin`` over data ``_get_org_infrastructure``
    already fetches; ``start``/``stop`` are user-surface routes in
    the platform infrastructure manager with no run-reachable counterpart;
    ``restart`` has no route in any repository; and ``logs`` is declared
    service-addressed against a pod-addressed route that returns 403 for all
    three of these services by policy.

    The design declares ``connect`` and ``status``. Only ``status`` is a named
    entry in ``tests/conformance/EXCEPTIONS.json``, saying what closes it; the
    other four are not, because an exception for an undeclared name is itself a
    blocking ``STALE_EXCEPTION``. That is why this assertion is on absence
    rather than on the exceptions file: it holds whether or not a verb is
    declared.

    A stub would put a green surface gate over an absent capability.
    """
    handle = getattr(_CLUSTER, name)
    for verb in ("status", "start", "stop", "restart", "logs"):
        assert not hasattr(handle, verb)


def test_resolution_is_cached_and_the_cache_does_not_re_synthesise() -> None:
    """Second access skips ``__getattr__`` entirely and returns the same object."""
    first = _CLUSTER.milvus
    assert _CLUSTER.milvus is first
    assert vars(_CLUSTER)["milvus"] is first


def test_from_import_spelling_resolves() -> None:
    from perceivo.cluster import elasticsearch

    assert elasticsearch is _CLUSTER.elasticsearch
    assert elasticsearch.connect is backend.get_elasticsearch


def test_import_module_spelling_fails_and_that_is_the_recorded_cost() -> None:
    """``import perceivo.cluster.milvus`` is the one spelling synthesis loses.

    The import machinery looks for a file. A ``MetaPathFinder`` or a
    ``sys.modules`` registration would restore it, and ``_catalog`` records why
    both are refused: registering on first access makes an import succeed or
    fail depending on what else in the process ran first. The supported
    spellings are attribute access — ``perceivo.cluster.milvus.connect`` — and
    ``from perceivo.cluster import milvus``.
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("perceivo.cluster.milvus")


# ---------------------------------------------------------------------------
# What the grid refuses, and what it says
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ray", "nodepools", "kafka", "postgres"])
def test_an_unreachable_service_is_refused_with_the_reachable_set(name: str) -> None:
    """The closed grid, stated. The design names ``ray`` and ``nodepools`` too.

    The reachable set in the message is read from the grid rather than written
    out; the design's other names are described rather than enumerated, because an
    enumeration in an error string is a snapshot no gate checks.
    """
    with pytest.raises(AttributeError) as caught:
        getattr(_CLUSTER, name)
    message = str(caught.value)
    assert ", ".join(sorted(_registry.SERVICES)) in message
    assert "perceivo.cluster.<service>.connect()" in message


@pytest.mark.parametrize(
    "name",
    [
        "_private",
        "__wrapped__",
        "_ipython_canary_method_should_not_exist_",
        "__custom_documentations__",
        "Milvus",
        "has-a-dash",
        "",
    ],
)
def test_probe_names_keep_hasattr_honest(name: str) -> None:
    """The import system, pytest, ``inspect`` and ``copy`` all probe with these.

    Answering ``__wrapped__`` with a synthesised module hands the interpreter a
    module where it expected a function, and the failure surfaces somewhere
    else entirely. The closed grid gets this for free, unlike ``_catalog``,
    which needs an explicit shape guard.
    """
    assert not hasattr(_CLUSTER, name)


@pytest.mark.parametrize("name", ["__path__", "__all__", "__name__", "__doc__"])
def test_real_package_dunders_are_untouched(name: str) -> None:
    """``__getattr__`` runs only for names that are not already bound."""
    assert not isinstance(getattr(_CLUSTER, name), types.ModuleType)


def test_the_removed_submodules_are_gone_from_the_package_directory() -> None:
    """The three ten-line re-export files, and the snapshot they carried.

    Asserted on the filesystem rather than by import: a leftover ``milvus.py``
    would shadow the synthesised handle for the ``import`` spelling only, so
    every attribute-access test above would still pass while two different
    objects answered to the same name.

    ``_backend.py`` and ``_mcp_backend.py`` hold the backends this plane
    forwards to. They are asserted by exact set rather than by an
    underscore-prefix rule for the reason above: the failure being caught is a *public* name
    appearing here, and "starts with an underscore" is a convention a new file
    can satisfy while still shadowing nothing this package intends to serve.
    """
    present = {p.name for p in Path(_CLUSTER.__path__[0]).glob("*.py")}
    assert present == {
        "__init__.py",
        "_backend.py",
        "_mcp_backend.py",
        "_registry.py",
    }
