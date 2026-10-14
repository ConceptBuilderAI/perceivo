"""An unreadable manifest beside a component whose type comes from elsewhere keeps VAL-TYPE.

Only ``perceivo validate component``, which looked the type up in that manifest,
reports the manifest alone; the library entry points judge the rest too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest
import yaml

from perceivo.validator import validate_orchestration
from perceivo.validator._paths import validate_component_files
from tests.unit.test_validator_orchestration_parity import _project
from tests.unit.test_validator_unreadable_files import UNREADABLE

# Declares no component type, so none is found once the manifest cannot be read.
_NO_TYPE = "class C:\n    async def run(self, inp):\n        return None\nENTRY_CLASS = 'C'\n"


def _typeless_node(tmp_path: Path) -> Path:
    project = _project(tmp_path, out_hop=True)
    orch = project / "orchestration.yaml"
    graph = yaml.safe_load(orch.read_text(encoding="utf-8"))
    for node in graph["nodes"]:
        if node.get("script") == "agent.py":
            node.pop("component_type", None)
    orch.write_text(yaml.safe_dump(graph), encoding="utf-8")
    return project


@pytest.mark.parametrize("make", UNREADABLE)
def test_a_typeless_node_with_an_unreadable_manifest_keeps_val_type(
    tmp_path: Path, make: Callable[[Path], str]
) -> None:
    project = _typeless_node(tmp_path)
    reason = make(project / "agent.yaml")
    report = validate_orchestration(str(project), validate_scripts=True)
    ids = sorted(c.id for c in report.checks if c.id.startswith("agent.py/"))
    assert ids == ["agent.py/MAN-FORMAT-001", "agent.py/VAL-ENTRY-001", "agent.py/VAL-TYPE-001"]
    (message,) = [c.message for c in report.checks if c.id == "agent.py/MAN-FORMAT-001"]
    assert message == f"The manifest cannot be read: {reason}"


@pytest.mark.parametrize("make", UNREADABLE)
def test_component_files_with_an_unreadable_manifest_keep_val_type(
    tmp_path: Path, make: Callable[[Path], str]
) -> None:
    script = tmp_path / "agent.py"
    script.write_text(_NO_TYPE, encoding="utf-8")
    manifest = script.with_suffix(".yaml")
    manifest.write_text("name: agent\nextension_type: AGENT\n", encoding="utf-8")
    reason = make(manifest)
    report = validate_component_files(script)
    assert {"MAN-FORMAT-001", "VAL-TYPE-001"} <= {c.id for c in report.checks}
    (message,) = [c.message for c in report.checks if c.id == "MAN-FORMAT-001"]
    assert message == f"The manifest cannot be read: {reason}"
