"""The four ``managed_service`` verbs that have no backend, and their withdrawal.

A forwarding function of the form
``return await ext.get_managed_service_status(instance_id)`` cannot be proven
by a delegation test that writes the method onto the stub first::

    stub_ext.get_managed_service_status = get_managed_service_status
    result = await managed_services.get_status("inst-1")

``stub_ext`` is a bare ``class _Stub: pass`` (``tests/conftest.py:118``), so
assigning an attribute to it makes any method exist. A test of the stub
alone asserts that the forwarding is spelled correctly and nothing more; it
never asks whether the far end exists, and for these verbs the far end does
not exist in any runtime. Counted over the whole platform trees:

===================================  ====  ========  =====
``ExtensionManager`` method          defs  mentions  local
===================================  ====  ========  =====
``list_managed_service_instances``      0         0      0
``get_managed_service_status``          0         0      0
``list_managed_service_backups``        0         0      0
``trigger_managed_service_backup``      0         0      0
``get_managed_service_connection``      0         0      0
``execute_database_query``              1         5      1
``invoke_ai_foundry``                   1         5      1
===================================  ====  ========  =====

So ``perceivo.managed_service.list()`` and the category grid's ``status`` /
``backups`` / ``backup`` would raise ``AttributeError`` on every call if
exported. They are withdrawn — not stubbed, per :mod:`perceivo.stream`'s
``captured`` precedent — and recorded as declared debt in
``tests/conformance/EXCEPTIONS.json``.

**What this module pins is the withdrawal, not the absence.** A test that
asserts "the platform lacks this method" would be testing another repository
from here. These tests assert the three things that are this package's to keep
true: a withdrawn name raises instead of resolving to something, no code under
``perceivo/`` calls any of the five, and every withdrawn symbol has a debt
record. The one measurement asserted directly is the local runtime's, because
``perceivo/local/`` is in this repository and is a real ``ExtensionManager``.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import perceivo
from perceivo.managed_service import _catalog

_MS = perceivo.managed_service

#: The ``ExtensionManager`` methods that exist in no runtime. Spelled here as
#: data so :func:`test_no_module_forwards_to_an_absent_platform_method` can scan
#: for them; this is the only list of the five in the test suite.
_ABSENT_EXT_METHODS = frozenset(
    {
        "get_managed_service_connection",
        "get_managed_service_status",
        "list_managed_service_backups",
        "list_managed_service_instances",
        "trigger_managed_service_backup",
    }
)

#: Backed methods, scanned for alongside the absent ones so the scanner is
#: shown to find what is there. A scan that reports "no forwards to the five"
#: because it never resolved an attribute would report exactly the same thing.
_PRESENT_EXT_METHODS = frozenset({"execute_database_query", "invoke_ai_foundry"})

_PERCEIVO = pathlib.Path(perceivo.__file__).parent
# ---------------------------------------------------------------------------
# The withdrawn names raise, and say where the verb went
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_catalog._WITHDRAWN))
def test_a_withdrawn_name_raises_rather_than_resolving(name: str) -> None:
    """The whole point of :data:`_catalog._WITHDRAWN`.

    Every one of these is a lowercase identifier, so an unqualified category
    grid would answer all six with a synthesised module handle and the caller
    would meet ``'module' object is not callable`` — an error about the wrong
    thing, one frame from where the mistake was.
    """
    with pytest.raises(AttributeError):
        getattr(_MS, name)


@pytest.mark.parametrize("name", sorted(_catalog._WITHDRAWN))
def test_the_error_names_the_platform_method_that_is_missing(name: str) -> None:
    """An actionable message is the value being bought by not stubbing.

    "No attribute" leaves the author of ``managed_service.list()`` guessing at a
    typo. Naming ``list_managed_service_instances`` makes the next question
    answerable, and pointing at ``EXCEPTIONS.json`` makes it answered.
    """
    with pytest.raises(AttributeError) as excinfo:
        getattr(_MS, name)
    message = str(excinfo.value)
    assert _catalog._WITHDRAWN[name] in message
    assert "not built" in message.lower()


def test_list_is_gone_from_the_export_contract() -> None:
    """The plane-level ``list`` verb is not exported.

    Asserted against ``__all__`` rather than ``dir()``: ``__all__`` is what the
    surface lock walks and what ``from perceivo.managed_service import *``
    honours, so it is the contract that a consumer can actually observe.
    """
    assert _MS.__all__ == ["ai", "database"]
    assert "list" not in _MS.__all__
    assert "list" not in dir(_MS)


def test_the_backend_module_is_deleted_not_merely_unexported() -> None:
    """Leaving ``_instances.py`` importable would leave the trap loaded.

    It is private, so nothing in the corpus could reach it — but anyone looking
    for a backend would find five functions with correct-looking bodies and
    could wire them up again, exporting verbs that fail on every call.
    """
    assert not (_PERCEIVO / "managed_service" / "_instances.py").exists()
    with pytest.raises(ImportError):
        __import__("perceivo.managed_service._instances")


# ---------------------------------------------------------------------------
# The grid survives the withdrawal, carrying nothing
# ---------------------------------------------------------------------------


def test_a_category_still_resolves_and_carries_no_verbs() -> None:
    """The grid is kept deliberately, and this is the shape it keeps.

    ``managed_service.<object>`` resolves to a handle with no verbs: the
    per-object verbs (``connect``, ``start``, ``stop``, ...) are declared debt in
    ``tests/conformance/EXCEPTIONS.json``, and the handle is what they will
    attach to when their backends land.
    """
    handle = _MS.cache
    assert not callable(handle), "an object segment is not callable"
    assert handle.__all__ == []
    assert _MS.cache is handle, "cached onto the package, not re-synthesised"


def test_a_verb_on_a_category_raises_and_names_the_category() -> None:
    """More precise than failing at the category: ``cache`` is real, ``status``
    is not, and the message says so in those terms."""
    with pytest.raises(AttributeError) as excinfo:
        getattr(_MS.cache, "status")  # noqa: B009 — the raise IS the assertion
    assert "perceivo.managed_service.cache" in str(excinfo.value)
    assert "status" in str(excinfo.value)


def test_database_keeps_connect_and_loses_the_re_exported_grid() -> None:
    """``connect`` survives because it is not generic.

    It resolves no credential and calls no absent method — it is a lazy handle
    over ``execute_database_query``, which measures 1 definition. The module
    re-exports nothing from the grid.
    """
    assert _MS.database.__all__ == ["connect"]
    for verb in ("status", "backups", "backup"):
        assert not hasattr(_MS.database, verb), verb


def test_reserved_and_withdrawn_cannot_disagree() -> None:
    """``_RESERVED`` is derived from ``_WITHDRAWN``, and this pins that.

    There is no backend whose export contract it could read live, so the
    alternative to a literal is no refusal at all; :mod:`perceivo.cluster._registry`
    makes the same trade for the same reason. Two hand-maintained collections would drift, one
    derived from the other cannot.
    """
    assert frozenset(_catalog._WITHDRAWN) == _catalog._RESERVED
    assert _catalog._CATEGORY_VERBS == ()


# ---------------------------------------------------------------------------
# No forward to an absent method survives anywhere in the package
# ---------------------------------------------------------------------------


def _attribute_names(path: pathlib.Path) -> set[str]:
    """Every attribute name accessed in *path*, e.g. ``ext.foo`` -> ``foo``.

    AST rather than text search, deliberately: this module and
    :mod:`perceivo.managed_service._catalog` both *name* all five methods in
    prose and in string literals, and a ``grep`` would match those and force the
    scan to be weakened with exclusions until it stopped meaning anything. An
    attribute access is a syntactic form; a docstring is not one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def test_no_module_forwards_to_an_absent_platform_method() -> None:
    """The invariant that keeps this from happening again.

    Not "these four verbs are gone" — that is one commit's worth of truth — but
    "no code in this package calls a method that exists in no runtime". A future
    ``managed_service.instances.list()`` built on the same absent method fails
    here even though every name in this file's parametrize list is untouched.
    """
    offenders = {
        str(path.relative_to(_PERCEIVO)): sorted(found)
        for path in sorted(_PERCEIVO.rglob("*.py"))
        if (found := _attribute_names(path) & _ABSENT_EXT_METHODS)
    }
    assert not offenders, (
        f"a module under perceivo/ accesses an ExtensionManager method that is "
        f"defined in no runtime — 0 definitions and 0 mentions in "
        f"the platform orchestration service, and none on "
        f"LocalExtensionManager. The call will raise AttributeError in every "
        f"cloud run. Withdraw the verb or build the method first: {offenders}"
    )


def test_the_scanner_can_see_a_forward_that_is_really_there() -> None:
    """A scan that finds nothing because it looks at nothing is not a control.

    ``perceivo/managed_service/_backend.py`` forwards to
    ``execute_database_query``, which is present in both runtimes and is the
    positive control for the whole measurement. If the walk cannot see it there,
    its silence above means nothing.
    """
    found: set[str] = set()
    for path in _PERCEIVO.rglob("*.py"):
        found |= _attribute_names(path) & _PRESENT_EXT_METHODS
    assert found == _PRESENT_EXT_METHODS, (
        f"the attribute scanner found {sorted(found)} of the backed methods it "
        f"should have found — it is not reading what it claims to read"
    )


def test_the_local_runtime_has_none_of_the_five() -> None:
    """The half of the measurement that lives in this repository.

    ``LocalExtensionManager`` is a real ``ExtensionManager``, not a stub that
    grows attributes on assignment, and it has no ``__getattr__`` — so this is
    the same question the cloud tree was asked, asked somewhere it can be
    asserted rather than cited.

    If this ever fails, the method was built: do not delete the assertion.
    Re-run the cloud count, and if it agrees, delete the ``EXCEPTIONS.json``
    entry and build the verb.
    """
    from perceivo.local.context import LocalExtensionManager

    assert not hasattr(LocalExtensionManager, "__getattr__")
    present = sorted(m for m in _ABSENT_EXT_METHODS if hasattr(LocalExtensionManager, m))
    assert not present, f"LocalExtensionManager now defines {present}"
    assert hasattr(LocalExtensionManager, "execute_database_query"), "control"


# ---------------------------------------------------------------------------
# Debt records, and preservation of failing calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["list", "get_status", "list_backups"])
async def test_a_call_that_failed_before_still_fails(name: str, stub_ext) -> None:
    """Preservation, in the direction that is easy to break by accident.

    A status change in *either* direction is a regression, and red-turning-green
    is the one a withdrawal could plausibly cause: if any of these had started
    returning an empty page or ``None``, an asset that has been failing loudly
    would begin silently reporting "no instances" and "no backups".

    Run with an execution context pushed, so the failure cannot be attributed to
    being outside a run — this is the in-run path, and it raises.
    """
    with pytest.raises(AttributeError):
        getattr(_MS, name)
