"""A graph exported from the platform warns only about keys nothing reads.

The fixture is a real platform export of a three-level nested orchestration.
Importing it and configuring every node of every level the way a local run does
must not name any key the web builder writes or the hosted service reads as
ignored. Keys no current builder writes and no hosted reader uses still warn.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import MagicMock

from perceivo.orchestration import load_bundle
from perceivo.runner._configuration import configure_ext_from_shell
from perceivo.runner.compiler import compile_project

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "orchestration_company_policy_chatbot_rag_nested.json"
)
_IGNORED = re.compile(r"Ignoring \d+ unrecognised node config key\(s\): (.+?)\. The runner")
_UNREAD = {
    "layer_policy_port_id",
    "paired_policy_input_port_id",
    "paired_tool_asset_id",
    "paired_tool_input_port_id",
}


def test_an_exported_graph_warns_only_about_keys_nothing_reads(tmp_path, caplog):
    project = load_bundle(FIXTURE, str(tmp_path))
    levels = sorted(project.rglob("orchestration.yaml"))
    assert len(levels) == 4, levels

    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    ext.get_node_secret_info.return_value = {"aliases": {}, "declarations": {}}
    configured = 0
    with caplog.at_level(logging.WARNING, logger="perceivo.runner.config"):
        for level in levels:
            for shell in compile_project(level.parent).shells_by_id.values():
                configure_ext_from_shell(ext, shell)
                configured += 1
    assert configured == 25

    warned: set[str] = set()
    for record in caplog.records:
        if record.levelname != "WARNING":
            continue
        match = _IGNORED.search(record.getMessage())
        assert match, record.getMessage()
        warned.update(match.group(1).split(", "))
    assert warned == _UNREAD
