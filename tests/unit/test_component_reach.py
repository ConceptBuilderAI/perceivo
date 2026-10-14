"""Declared planes must be reachable the way components reach them.

A component's only reach is attribute access: ``create_sandbox_globals()`` does
one bare ``import perceivo`` and injects the module object as the ``perceivo``
global. A module that is importable but never bound as an attribute of the
package raises ``AttributeError`` for every caller on the platform, while a
test that runs ``import perceivo.mcp`` first -- the act that binds the
attribute -- passes, because it proves the route for a caller who has already
done the one thing no component does. See ``fixtures_component_reach`` for why the
probe has to be a subprocess: after any test in the session imports the
submodule, ``sys.modules`` binds it for every later assertion too, so an
in-process check would only make the outcome depend on collection order.
"""

from __future__ import annotations

import functools

import pytest

from .fixtures_component_reach import reachable_from_bare_import


#: Every name the package **declares** must be
#: reachable the way a component reaches it, and ``__all__`` is that
#: declaration: the platform derives the whole plane set its sandbox offers
#: from it. A declared
#: name a component cannot reach is a plane the sandbox offers and the runtime
#: cannot serve.
#:
#: Parameterised from the live ``__all__`` rather than a hardcoded list, so a
#: name added to the declaration that a bare import cannot reach fails here.
def _declared_names() -> list[str]:
    import perceivo

    return sorted(perceivo.__all__)


@functools.lru_cache(maxsize=1)
def _declared_reach() -> dict[str, bool]:
    """One probe for the whole declaration, not one per name.

    The probe costs a fresh interpreter, so probing each name separately spends
    one interpreter per name to answer a question one subprocess answers.
    The names are independent -- attribute resolution of one cannot affect
    another -- so batching them changes nothing but the cost. Parametrisation
    stays per-name so a failure still names the plane that broke.
    """
    return reachable_from_bare_import(*_declared_names())


@pytest.mark.parametrize("name", _declared_names())
def test_declared_name_is_reachable_from_a_bare_import(name: str) -> None:
    """A declared name a component cannot reach is not exposed, only listed."""
    assert _declared_reach()[name], (
        f"perceivo.{name} is in `perceivo.__all__` but a component cannot reach "
        f"it after a bare `import perceivo`. Every declared name must resolve "
        f"as an attribute of the package after that import alone."
    )


def test_mcp_call_path_is_reachable_without_importing_the_submodule() -> None:
    """The MCP call path, stated as a component experiences it.

    ``perceivo.cluster.mcp`` is served by the cluster package's ``__getattr__``,
    so it must resolve -- handle and verb -- after nothing but a bare
    ``import perceivo``, which is all a component's sandbox does.
    """
    reach = reachable_from_bare_import("cluster.mcp", "cluster.mcp.connect")
    unreachable = [n for n, ok in reach.items() if not ok]
    assert not unreachable, (
        f"unreachable from a bare `import perceivo`: "
        f"{', '.join('perceivo.' + n for n in unreachable)}. MCP is reached as "
        f"perceivo.cluster.mcp.connect(), and a component reaches the SDK only "
        f"by attribute."
    )


def test_the_probe_can_still_report_a_removed_module() -> None:
    """Guard on the guard: the tests above are only worth their pass if this holds.

    The SDK has no ``perceivo.infra``. If the probe reported it reachable,
    every assertion built on it would be passing for the wrong reason.
    """
    reach = reachable_from_bare_import("infra", "infra.mcp.call_tool")
    assert not reach["infra"], "the SDK has no perceivo.infra"
    assert not reach["infra.mcp.call_tool"], "perceivo.infra.mcp.call_tool must not resolve"
