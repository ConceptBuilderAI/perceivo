"""Graph-declared paths stay inside the project.

``orchestration.yaml`` is untrusted input: a project is a thing you clone, get
handed as a sample, or install as a template. Three code paths joined a value
straight off the graph onto the project directory and read the result:

* ``_bundle/orchestration.py`` -- a node's ``script`` / ``manifest``, on the
  ``perceivo export`` and ``perceivo.orchestration.publish()`` path,
* the same module's ``_child_project`` -- a nested node's ``project``, which
  compounds, because every script the escaped child names is then read too,
* ``validator/_orchestration/_loader.py`` -- ``script`` / ``manifest`` again, on
  the ``validate`` path.

``script: ../../../.ssh/id_rsa`` therefore packed the victim's private key into
the bundle, and ``publish()`` POSTed it to ``/v1/orchestrations/import``
under the victim's own API key. **No project code runs** on any of those paths --
export, publish and validate are what a cautious user reaches for *instead of*
running an unfamiliar project -- so "you already had code execution" was never a
defence.

These tests pin the exfiltration, not the implementation: each writes a sentinel
outside the project, asserts it is genuinely outside and readable (otherwise a
"blocked" result would be vacuous), and then proves the sentinel's bytes cannot
reach a bundle. The last two tests are the other half of the contract -- a
legitimate subdirectory path must still work, or containment has broken export
rather than secured it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._internal.paths import PathTraversalError, resolve_within_project
from perceivo.types.errors import PerceivoError
from perceivo.validator._orchestration._loader import _build_component

SENTINEL = "SENTINEL_PRIVATE_KEY_MATERIAL_XYZ123"


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def victim(tmp_path: Path) -> Path:
    """A secret file beside -- not inside -- the shared project directory."""
    secret = tmp_path / "victim_secret.pem"
    secret.write_text(f"{SENTINEL}\n", encoding="utf-8")
    project = tmp_path / "shared_project"
    project.mkdir()

    # Without these the tests pass whether or not containment holds: a sentinel that
    # is absent, unreadable, or actually inside the project cannot be exfiltrated
    # by any code, and "blocked" would mean nothing.
    assert secret.read_text(encoding="utf-8").strip() == SENTINEL
    assert project.resolve() not in secret.resolve().parents
    return project


def _write(project: Path, orch: dict) -> None:
    (project / "orchestration.yaml").write_bytes(yaml.safe_dump(orch, sort_keys=False).encode())


def _node(**over: object) -> dict:
    node: dict[str, object] = {"id": "n1", "name": "n1", "component_type": "TOOL"}
    node.update(over)
    return node


# --------------------------------------------------------------------------- #
# the three vectors                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "escape",
    [
        "../victim_secret.pem",
        "./../victim_secret.pem",
        "sub/../../victim_secret.pem",
    ],
    ids=["parent", "dot-parent", "descend-then-escape"],
)
def test_export_refuses_a_script_outside_the_project(victim: Path, escape: str) -> None:
    _write(victim, {"name": "shared", "nodes": [_node(script=escape)]})

    with pytest.raises(PathTraversalError) as err:
        orchestration_to_bundle(victim)

    # The author who typo'd the path has to be able to find the node.
    assert "n1" in str(err.value)
    assert SENTINEL not in str(err.value)


def test_export_refuses_a_manifest_outside_the_project(victim: Path) -> None:
    (victim / "tool.py").write_text("class T:\n    pass\n", encoding="utf-8")
    _write(
        victim,
        {
            "name": "shared",
            "nodes": [_node(script="tool.py", manifest="../victim_secret.pem")],
        },
    )

    with pytest.raises(PathTraversalError):
        orchestration_to_bundle(victim)


def test_export_refuses_a_nested_project_outside_the_project(victim: Path) -> None:
    """The compounding vector: an escaped child's own scripts are read next."""
    outside = victim.parent / "outside_project"
    outside.mkdir()
    _write(outside, {"name": "child", "nodes": []})
    _write(
        victim,
        {
            "name": "shared",
            "nodes": [_node(component_type="ORCHESTRATION", project="../outside_project")],
        },
    )

    with pytest.raises(PathTraversalError):
        orchestration_to_bundle(victim)


def test_validate_refuses_a_script_outside_the_project(victim: Path) -> None:
    with pytest.raises(PathTraversalError):
        _build_component({"id": "n1", "script": "../victim_secret.pem"}, victim)


def test_export_refuses_an_absolute_script(victim: Path) -> None:
    """An absolute path re-roots ``Path.__truediv__`` entirely, escaping without ``..``."""
    absolute = str((victim.parent / "victim_secret.pem").resolve())
    _write(victim, {"name": "shared", "nodes": [_node(script=absolute)]})

    with pytest.raises(PathTraversalError):
        orchestration_to_bundle(victim)


def test_the_sentinel_never_reaches_a_bundle(victim: Path) -> None:
    """The end-to-end property, stated as the leak rather than as a call shape."""
    _write(victim, {"name": "shared", "nodes": [_node(script="../victim_secret.pem")]})

    try:
        bundle = orchestration_to_bundle(victim)
    except PathTraversalError:
        return
    pytest.fail(f"no exception, and the bundle rendered as: {bundle!r}"[:400])


# --------------------------------------------------------------------------- #
# the other half: legitimate paths must be unaffected                          #
# --------------------------------------------------------------------------- #


def test_a_subdirectory_script_still_exports(victim: Path) -> None:
    (victim / "sub").mkdir()
    (victim / "sub" / "tool.py").write_text("class T:\n    pass\n", encoding="utf-8")
    (victim / "sub" / "tool.yaml").write_text(
        yaml.safe_dump({"extension_type": "TOOL", "name": "tool"}), encoding="utf-8"
    )
    _write(
        victim,
        {
            "name": "good",
            "nodes": [_node(script="sub/tool.py", manifest="sub/tool.yaml")],
        },
    )

    bundle = orchestration_to_bundle(victim)

    assert bundle["components"], "a legitimate in-project script produced no component"


def test_a_missing_in_project_script_still_reports_as_missing(victim: Path) -> None:
    """Containment must not swallow the ordinary typo into a security error."""
    _write(victim, {"name": "good", "nodes": [_node(script="does_not_exist.py")]})

    with pytest.raises(FileNotFoundError):
        orchestration_to_bundle(victim)


# --------------------------------------------------------------------------- #
# the primitive                                                                #
# --------------------------------------------------------------------------- #


def test_traversal_error_is_catchable_as_both_bases() -> None:
    """``except PerceivoError`` catches everything the SDK raises."""
    assert issubclass(PathTraversalError, PerceivoError)
    assert issubclass(PathTraversalError, ValueError)


def test_the_local_adapters_import_site_is_the_same_class() -> None:
    """One traversal error, not two -- the second is the one that goes unfixed."""
    from perceivo.local.adapters._safe_path import PathTraversalError as adapters_error

    assert adapters_error is PathTraversalError


def test_an_empty_value_does_not_resolve_to_the_project_root(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError):
        resolve_within_project(tmp_path, "", field="script", node_id="n1")
