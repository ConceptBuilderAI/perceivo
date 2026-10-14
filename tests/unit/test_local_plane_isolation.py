"""The local plane is absent from a runner's module graph.

None of the local plane may be loaded inside any of the five service runners,
and ``perceivo/__init__.py`` **MUST NOT** eagerly import any of it. Imported
eagerly, the local plane is more than half the modules a bare ``import perceivo``
executes, so every service runner would carry ``perceivo.runner.loader``, whose
``spec.loader.exec_module`` is unsandboxed execution, loaded before any
component runs.

**What this file does and does not claim.** Lazy binding is not an access
control: a lazy module is still importable. The enforcement is the
``BLOCKED_INTERNAL_PREFIXES`` chokepoint in each of the five runtimes, which
lives in those runtimes and is asserted there. What lazy binding buys, and what
is asserted here, is that the code is **absent until something asks for it by
name**, which makes the runtimes' blocklist the thing that decides rather than a
statement about code that is loaded either way.

**Why every assertion runs in a subprocess.** The property is about what
``sys.modules`` contains after one bare import, and nothing in-process can
measure that: by the time a test body runs, pytest has collected a package whose
conftest and sibling modules have imported half the SDK. An in-process version
of this file would be a test whose outcome depends on collection order -- the
exact shape ``tests/unit/fixtures_component_reach.py`` was written to escape,
after two tests shipped green by importing the submodule on the line above the
assertion.
"""

from __future__ import annotations

import pytest

from tests.unit.fixtures_component_reach import (
    loaded_after_bare_import,
    reachable_from_bare_import,
)

#: The local plane. These are the seven ``perceivo/__init__.py`` must
#: not import eagerly, and the seven ``_LOCAL_PLANE`` binds lazily.
LOCAL_PLANE = (
    "bootstrap",
    "component",
    "config",
    "local",
    "orchestration",
    "runner",
    "validator",
)


def test_the_local_plane_is_absent_after_a_bare_import() -> None:
    """The absence, stated as a runner experiences it."""
    loaded = loaded_after_bare_import()
    present = sorted(m for m in loaded if m.split(".")[1] in set(LOCAL_PLANE))
    assert present == [], (
        f"{len(present)} local-plane modules are in the module graph after a bare "
        f"``import perceivo``, so the local plane is not lazily bound: {present[:8]}"
    )


def test_the_runtime_planes_are_still_loaded_after_a_bare_import() -> None:
    """The positive control, and it is not optional.

    "Zero local-plane modules" is what a broken probe reports too -- one that
    imported nothing, or resolved a package with no submodules at all. This
    fixes the other side: the runtime planes a component actually uses must be
    eagerly present, so the assertion above is measuring a deliberate absence
    rather than a general one.
    """
    loaded = loaded_after_bare_import()
    for name in ("perceivo.log", "perceivo.files", "perceivo.stream", "perceivo.secrets"):
        assert name in loaded, f"{name} is not eagerly imported; the probe is broken"


@pytest.mark.parametrize("name", LOCAL_PLANE)
def test_each_local_plane_module_is_still_reachable_by_attribute(name: str) -> None:
    """Lazy is not hidden.

    A component reaches a plane by attribute on the injected module object and
    by no other means, so a plane taken off the eager list and *not* bound by
    ``__getattr__`` would be a removal wearing an optimisation's clothes.
    ``perceivo.bootstrap`` is the case that would bite first: it belongs to the
    local plane and the corpus calls it.
    """
    assert reachable_from_bare_import(name)[name] is True


def test_the_lazy_binding_does_not_reopen_a_removed_name() -> None:
    """``__getattr__`` is a closed allowlist, not a fallthrough to ``importlib``.

    The one-line version of this hook -- ``return importlib.import_module("." +
    name, __name__)`` -- passes every assertion above while binding any
    submodule that happens to exist, and turns an ``AttributeError`` for a typo
    into an ``ImportError`` from three frames deeper. The public surface is a
    property of what does *not* resolve as much as what does, so it is asserted here rather than
    inferred from the table's contents.
    """
    removed = ("storage", "chat", "workload", "evaluations", "infra", "notebook")
    reach = reachable_from_bare_import(*removed)
    still_resolving = sorted(n for n, ok in reach.items() if ok)
    assert still_resolving == [], (
        f"{still_resolving} resolved on the module object. The clean break is "
        f"only clean while ``__getattr__`` refuses every name outside "
        f"``_LOCAL_PLANE``."
    )


def test_asking_for_one_plane_does_not_drag_in_the_others() -> None:
    """The tier is seven independent modules, not one module in seven pieces.

    If ``perceivo.config`` imported ``perceivo.runner``, a component touching
    config would pull ``loader.exec_module`` into the graph anyway and the absence
    would hold only for callers who touch nothing. ``config`` is the right probe
    because it is the plane the other six read.
    """
    loaded = loaded_after_bare_import(then_getattr="config")
    assert "perceivo.config" in loaded, "the probe did not bind the plane it asked for"
    dragged = sorted(
        m for m in loaded if m.split(".")[1] in set(LOCAL_PLANE) and m.split(".")[1] != "config"
    )
    assert dragged == [], f"reaching perceivo.config also loaded {dragged}"
