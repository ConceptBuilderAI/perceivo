"""Rule 7 of ``scripts/vendor_template_library.py``: drop ``log=`` from Output calls.

The ``log`` field is gone from every Output type, so a vendored template that
still passes it raises ``TypeError`` on its first run. The rule deletes the
whole-line keyword, leaves every other call alone, and refuses a shape it cannot
remove by whole lines rather than guessing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import perceivo
from scripts import vendor_template_library as vendor

LIBRARY = Path(perceivo.__file__).with_name("templates") / "_library"
STEM = "database-connector"

SCRIPT = """\
def run():
    return perceivo.primitives.AgentOutput(
        artifact=a,
        log=f"{op} done",
        response_data=d,
    )
"""

STRIPPED = """\
def run():
    return perceivo.primitives.AgentOutput(
        artifact=a,
        response_data=d,
    )
"""


def test_whole_line_log_keyword_removed() -> None:
    assert vendor.rewrite_text(SCRIPT, STEM) == STRIPPED


def test_multi_line_log_keyword_removed_whole() -> None:
    script = "ToolOutput(\n    log=dumps({\n        'a': 1,\n    }),\n    response_data=d,\n)\n"
    assert vendor.strip_output_log(script) == "ToolOutput(\n    response_data=d,\n)\n"


def test_crlf_script_keeps_its_line_endings() -> None:
    script = SCRIPT.replace("\n", "\r\n")
    assert vendor.strip_output_log(script) == STRIPPED.replace("\n", "\r\n")


def test_non_output_call_is_left_alone() -> None:
    script = "record = SnapshotContext(\n    log=engine.log,\n)\n"
    assert vendor.strip_output_log(script) == script


def test_conversation_log_is_left_alone() -> None:
    script = "AgentOutput(\n    artifact=a,\n    conversation_log=entry,\n)\n"
    assert vendor.strip_output_log(script) == script


def test_rule_7_is_off_when_not_selected() -> None:
    assert vendor.rewrite_text(SCRIPT, STEM, rules=(1, 2, 3, 5, 6)) == SCRIPT


def test_inline_log_keyword_refuses_the_vendor() -> None:
    script = "AgentOutput(artifact=a, log='x')\n"
    with pytest.raises(SystemExit, match="rule 7"):
        vendor.strip_output_log(script)


def test_unparseable_output_string_refuses_the_vendor() -> None:
    script = "class {name}:\n    AgentOutput(\n        log=x,\n    )\n"
    with pytest.raises(SystemExit, match="does not parse"):
        vendor.strip_output_log(script)


def test_rule_7_is_recorded_in_source_json() -> None:
    source = json.loads((LIBRARY / "SOURCE.json").read_text(encoding="utf-8"))
    assert source["rules"] == list(vendor.RULES)
    assert 7 in vendor.RULES


@pytest.mark.parametrize("name", ["database-connector", "llm-request"])
def test_vendored_library_passes_no_log(name: str) -> None:
    data = (LIBRARY / f"{name}.cbcomponent.json").read_bytes()
    assert data.count(b"log=") == 0
