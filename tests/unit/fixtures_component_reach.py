"""Reach a name the way a component reaches it, not the way a test can.

A component never imports a submodule. The platform's component sandbox does a
single bare ``import perceivo`` and injects **the module object itself** as the
``perceivo`` global, so attribute access is the only reach a component has. A
submodule that is not bound as an attribute of the package raises
``AttributeError`` there regardless of how importable it is.

That distinction cannot be measured in-process, and the attempt is what let a
regression ship green. ``test_plane_restructure.py`` and
``test_cluster_registry.py`` both asserted *"the surviving route must work"* --
each after an ``import perceivo.mcp`` on the line above, which is precisely the
act that binds the attribute. The assertion could not fail: it created the
condition it was testing for, and it did so on behalf of a caller who cannot.
Once **any** test in the session imports the submodule, ``sys.modules`` caching
binds it for every later assertion too, so even moving the import out of the
test body would only make the outcome depend on collection order.

So the probe runs in a fresh interpreter, and it asserts in both directions:
a name known reachable must come back reachable, and a name known absent must
come back unreachable. A probe that cannot report failure proves nothing when it
reports success.

The editable installs are scrubbed, and deliberately **not** asserted on. A
developer machine can carry ``__editable__.perceivo-*.finder`` entries on
``sys.path``; they register
``_EditableFinder`` on ``sys.meta_path`` *after* ``PathFinder``, so they never
shadow a module this checkout ships, but they resolve names it does **not** ship
out of a foreign tree. Attribute-only resolution mostly sidesteps them already
-- ``getattr`` triggers no import except through the package's own
``__getattr__`` -- and removing them costs nothing, so the probe removes them.
Requiring the removal to have matched something would be the opposite of
hermetic: it would pass on this machine and fail in CI, which installs no
editable copy. The guard that holds in both places is the wrong-tree check
below, which compares the resolved ``perceivo.__file__`` against this checkout
and refuses to report on a package nobody is editing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TypeGuard

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import json, sys

removed = [f for f in sys.meta_path
           if getattr(f, "__module__", "").startswith("__editable__")]
sys.meta_path[:] = [f for f in sys.meta_path if f not in removed]

sys.path.insert(0, {root!r})

import perceivo
from pathlib import Path

where = Path(perceivo.__file__).resolve()
if Path({root!r}).resolve() not in where.parents:
    print(json.dumps({{"error": "wrong tree: " + str(where)}}))
    raise SystemExit(0)

out = {{}}
for name in {names!r}:
    obj = perceivo
    ok = True
    for part in name.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            ok = False
            break
    out[name] = ok

print(json.dumps({{"reach": out, "scrubbed": len(removed),
                  "version": perceivo.__version__}}))
"""


#: Reachable on the SDK this checkout ships, and absent on it. Appended to
#: every probe so no caller can forget them.
#:
#: A control pinned to a name that leaves the surface makes every probe in
#: this module raise CONTROL FAILED, which reports "the probe is broken" for
#: what is really a surface change. ``params.get`` is the control because it is
#: declared in ``__all__`` and is part of the public surface. Repoint this to
#: another such name if that ever stops holding -- do **not** relax the control.
_POSITIVE_CONTROL = "params.get"
_NEGATIVE_CONTROL = "definitely_not_a_plane.nope"


_GRAPH_PROBE = r"""
import json, sys

removed = [f for f in sys.meta_path
           if getattr(f, "__module__", "").startswith("__editable__")]
sys.meta_path[:] = [f for f in sys.meta_path if f not in removed]

sys.path.insert(0, {root!r})

import perceivo
from pathlib import Path

where = Path(perceivo.__file__).resolve()
if Path({root!r}).resolve() not in where.parents:
    print(json.dumps({{"error": "wrong tree: " + str(where)}}))
    raise SystemExit(0)

# Snapshot BEFORE any attribute is touched: a lazy plane binds on access, so
# reading one here would be the measurement causing what it measures.
loaded = sorted(m for m in sys.modules if m.startswith("perceivo."))

after = None
if {then!r} is not None:
    getattr(perceivo, {then!r})
    after = sorted(m for m in sys.modules if m.startswith("perceivo."))

print(json.dumps({{"loaded": loaded, "after": after}}))
"""


def _is_str_list(value: object) -> TypeGuard[list[str]]:
    """Whether a subprocess JSON value is a list containing only strings."""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_reach_map(value: object) -> TypeGuard[dict[str, bool]]:
    """Whether a subprocess JSON value is the reachability map we requested."""
    return isinstance(value, dict) and all(
        isinstance(name, str) and isinstance(reached, bool) for name, reached in value.items()
    )


def loaded_after_bare_import(then_getattr: str | None = None) -> list[str]:
    """Return the ``perceivo.*`` modules a single bare ``import perceivo`` executes.

    This is the module *graph*, not the reachable surface, and the difference is
    the whole point of lazy binding: a lazily bound plane is reachable and
    absent, which is exactly the state the check asks for.

    With ``then_getattr``, one attribute is read after the snapshot and the
    post-access graph is returned instead -- how the fan-out of touching a
    single lazy plane is measured.

    The two controls are the same ones :func:`reachable_from_bare_import`
    applies, for the same reason. A probe returning an empty list would satisfy
    every "the local plane is absent" assertion ever written against it.
    """
    code = _GRAPH_PROBE.format(root=str(REPO_ROOT), then=then_getattr)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(
            f"module-graph probe failed (rc={proc.returncode}):\n{proc.stderr.strip()}"
        )
    payload: object = json.loads(proc.stdout.strip().splitlines()[-1])
    if not isinstance(payload, dict):
        raise AssertionError(f"module-graph probe returned a non-object payload: {payload!r}")
    if "error" in payload:
        raise AssertionError(
            f"module-graph probe resolved the wrong package: {payload['error']}. "
            f"Expected a perceivo under {REPO_ROOT}."
        )

    loaded = payload["after"] if then_getattr is not None else payload["loaded"]
    if not _is_str_list(loaded):
        raise AssertionError(f"module-graph probe returned invalid module names: {loaded!r}")
    if not loaded:
        raise AssertionError(
            "CONTROL FAILED: the probe reports an empty module graph, so it "
            "cannot report a present module either"
        )
    if "perceivo.types" not in loaded:
        raise AssertionError(
            "CONTROL FAILED: perceivo.types is absent from the graph. It is "
            "imported by ``perceivo/__init__.py`` unconditionally, so the probe "
            "is broken rather than the tree under test"
        )
    return loaded


def reachable_from_bare_import(*names: str) -> dict[str, bool]:
    """Map each dotted name to whether a component could reach it.

    Resolution is attribute-only, in a fresh interpreter, against this checkout.
    The probe's own controls are appended here rather than left to the caller:
    a probe that quietly stopped reporting ``AttributeError`` would turn every
    test built on it green, which is the failure this module exists to prevent.
    """
    probed = [*names, _POSITIVE_CONTROL, _NEGATIVE_CONTROL]
    code = _PROBE.format(root=str(REPO_ROOT), names=probed)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f"reach probe failed (rc={proc.returncode}):\n{proc.stderr.strip()}")
    payload: object = json.loads(proc.stdout.strip().splitlines()[-1])
    if not isinstance(payload, dict):
        raise AssertionError(f"reach probe returned a non-object payload: {payload!r}")
    if "error" in payload:
        raise AssertionError(
            f"reach probe resolved the wrong package: {payload['error']}. "
            f"Expected a perceivo under {REPO_ROOT}."
        )

    reach = payload["reach"]
    if not _is_reach_map(reach):
        raise AssertionError(f"reach probe returned an invalid reachability map: {reach!r}")
    if reach.get(_POSITIVE_CONTROL) is not True:
        raise AssertionError(
            f"CONTROL FAILED: perceivo.{_POSITIVE_CONTROL} is unreachable, so the "
            f"probe is broken rather than the name under test"
        )
    if reach.get(_NEGATIVE_CONTROL) is not False:
        raise AssertionError(
            f"CONTROL FAILED: perceivo.{_NEGATIVE_CONTROL} resolved. The probe "
            f"cannot report a failure, so it cannot report a pass either"
        )
    return {n: reach[n] for n in names}
