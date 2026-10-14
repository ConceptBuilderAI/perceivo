"""``perceivo import-bundle --members`` never copies a member's local state into ``nested/``.

A member project on disk may hold ``.env`` files (its author's secrets) and a
``.perceivo/`` home (its local runs). The embedded copy under the imported
project's ``nested/`` carries the graph and nothing else.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from perceivo._bundle.capability import capability_to_project, member_name
from perceivo._cli import main as cli

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "capability_company_policy_chatbot.json"
)
PLAIN = "Company Policy Chatbot - Plain"
RAG = "Company Policy Chatbot - RAG"
UTF8 = "utf-8"
LOCAL_STATE = (".env", ".env.local", ".perceivo")


def _plant(member: Path) -> None:
    (member / ".env").write_text("MARKER=local-only\n", encoding=UTF8)
    (member / ".env.local").write_text("MARKER=local-only\n", encoding=UTF8)
    runs = member / ".perceivo" / "runs"
    runs.mkdir(parents=True)
    (runs / "r1.json").write_text("{}", encoding=UTF8)


def _import_router(tmp_path: Path, *, plant: bool) -> list[Path]:
    """Import a router bundle whose node references the RAG member; return nested children."""
    capability = json.loads(FIXTURE.read_text(encoding=UTF8))
    root = capability_to_project(capability, tmp_path / "cap")
    members = yaml.safe_load((root / "capability.yaml").read_text(encoding=UTF8))["members"]
    if plant:
        _plant(root / next(entry["project"] for entry in members if entry["name"] == RAG))
    router = copy.deepcopy(next(m for m in capability["members"] if member_name(m) == PLAIN))
    router["orchestration"]["name"] = router["new_name"] = "Router"
    router["nodes"].append(
        {
            "local_id": "node_m0",
            "name": "N",
            "component_ref": "orch_0",
            "orchestration_asset_id": f"@member:{RAG}",
            "config": {},
        }
    )
    source = tmp_path / "router.json"
    source.write_text(json.dumps(router), encoding=UTF8)
    args = ["import-bundle", str(source), "--output", str(tmp_path / "out"), "--members", str(root)]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    nested = Path(result.output.strip().splitlines()[-1]) / "nested"
    return sorted(nested.iterdir())


@pytest.mark.parametrize("plant", [True, False], ids=["planted", "control"])
def test_member_copy_carries_the_graph_and_no_local_state(tmp_path, plant):
    children = _import_router(tmp_path, plant=plant)
    assert len(children) == 1
    child = children[0]
    assert (child / "orchestration.yaml").is_file()
    assert [name for name in LOCAL_STATE if (child / name).exists()] == []
