"""The template catalog is the platform library, and every entry runs.

(a) **Names.** The library entries are exactly the platform template library's
eighteen components and five orchestrations; the SDK's orchestration scaffolds may
sit beside them under names that do not collide.

(b) **Runnable.** For every entry: ``templates.use``, then the public path
validator on what it wrote, then a local run -- ``runner.run_component`` for a
component, ``runner.run_orchestration`` for an orchestration, after
:func:`~tests.unit._template_harness.fill_slots` on a copy for a slot template.

(c) **Provenance.** Each vendored file's sha256 is ``SOURCE.json``'s
``vendored_sha256``; each component copy's manifest carries the library master's
``template_source_id``; nothing ``use`` writes carries the System label.

The planted controls prove each check can fail; the harness control proves the
slot fill is doing the work.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

import perceivo
from perceivo.runner import run_component, run_orchestration
from perceivo.templates import _catalog
from scripts import vendor_template_library as vendor
from tests.start_input_fit import fit_start_input
from tests.unit._template_harness import fill_slots, stubs_that_ran

LIBRARY = Path(_catalog.__file__).with_name("_library")
SOURCE = json.loads((LIBRARY / "SOURCE.json").read_text(encoding="utf-8"))

LIBRARY_COMPONENTS = {
    "ai-foundry-gpt-5-nano-agent",
    "ai-foundry-gpt-5.2-agent",
    "chat-channel-gateway",
    "context-builder",
    "data-adapter",
    "database-connector",
    "delay-wait",
    "elasticsearch",
    "embedding",
    "files",
    "generic-end",
    "generic-start",
    "http-request",
    "json-handler",
    "llm-request",
    "mcp-tool-runner",
    "milvus",
    "redis-cache",
}
LIBRARY_ORCHESTRATIONS = {
    "conditional-router",
    "error-handler",
    "iterator",
    "parallel-executor",
    "sequential-executor",
}
#: Open FSM state ports each library orchestration ships, and how many of the
#: stubs wired to them the run must reach (the router takes one branch).
SLOTS = {
    "conditional-router": (2, 1),
    "error-handler": (0, 0),
    "iterator": (1, 1),
    "parallel-executor": (2, 2),
    "sequential-executor": (4, 4),
}
COMPONENT_INPUT = {"prompt": "hi", "message": "hi", "data": {"a": 1}, "query": "select 1"}
#: A template that refuses to run unconfigured -- each says which setting it lacks --
#: is run with the settings a builder would give it in the Inspector (written as the
#: used copy's manifest defaults) and the fields its settings read.
CONFIGURED: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "chat-channel-gateway": ({"platform": "slack", "recipient": "#ops", "message": "hi"}, {}),
    "context-builder": ({}, {"sources": [{"text": "Paris is in France."}]}),
    "database-connector": ({"platform_database_id": "db-1", "table_name": "users"}, {}),
    "elasticsearch": ({"index_name": "docs"}, {}),
    "embedding": ({}, {"chunks": ["hi"]}),
    # A file the local mock seeds into the org storage root.
    "files": ({}, {"file_path": "story_beginning.txt"}),
    "json-handler": (
        {"array_path": "rows", "filter_field": "a", "filter_value": "1"},
        {"rows": [{"a": "1"}, {"a": "2"}]},
    ),
    "milvus": ({"collection_name": "docs"}, {"embeddings": [[0.1, 0.2]], "texts": ["hi"]}),
    "redis-cache": ({}, {"key": "k"}),
}
#: Start input for an orchestration whose first step reads more than a prompt.
ORCHESTRATION_INPUT = {"iterator": {"items": [1, 2]}}

ALL = [t.name for t in perceivo.templates.list()]


def _errors(report: Any) -> list[str]:
    return [c.id for c in report.errors]


def _exercise(name: str, into: Path) -> dict[str, Any]:
    """(b) for one entry: use -> validate -> run. Returns what happened."""
    template = perceivo.templates.get(name)
    written = perceivo.templates.use(name, into=str(into / "used"))
    if template.kind == "component":
        report = perceivo.validator.validate_component(written)
        script = next(written.glob("*.py"))
        assert template.component_type is not None
        params, extra = CONFIGURED.get(name, ({}, {}))
        if name == "http-request":
            params = {"base_url": _loopback_server()}
        _set_defaults(script.with_suffix(".yaml"), params)
        result = asyncio.run(
            run_component(
                str(script),
                component_type=template.component_type,
                manifest_path=str(script.with_suffix(".yaml")),
                input_data={**COMPONENT_INPUT, **extra},
            )
        )
        return {"errors": _errors(report), "result": result, "stubs": [], "ran": []}
    report = perceivo.validator.validate_orchestration(written)
    filled, stubs = fill_slots(written, into / "filled")
    filled_report = perceivo.validator.validate_orchestration(filled)
    result = asyncio.run(
        run_orchestration(
            str(filled),
            input_data=fit_start_input(filled, ORCHESTRATION_INPUT.get(name, {"prompt": "hi"})),
        )
    )
    return {
        "errors": _errors(report) + _errors(filled_report),
        "result": result,
        "stubs": stubs,
        "ran": stubs_that_ran(result, stubs),
    }


def _loopback_server() -> str:
    """A one-route JSON server on 127.0.0.1 for the duration of the test process."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


def _set_defaults(manifest: Path, values: dict[str, Any]) -> None:
    """Give the used copy's declared parameters *values* as their defaults."""
    if not values:
        return
    doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    declared = {p["name"]: p for p in doc["input_parameters"]}
    for key, value in values.items():
        declared[key]["default"] = value
    manifest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _with_bundle_edit(monkeypatch: pytest.MonkeyPatch, name: str, edit: Any) -> None:
    """Serve library entry *name* through ``use`` with *edit* applied to its strings."""
    original = _catalog._library_bundle

    def edited(entry: str) -> dict[str, Any]:
        bundle = original(entry)
        return _map_strings(bundle, edit) if entry == name else bundle

    monkeypatch.setattr(_catalog, "_library_bundle", edited)


def _map_strings(value: Any, edit: Any) -> Any:
    if isinstance(value, str):
        return edit(value)
    if isinstance(value, list):
        return [_map_strings(v, edit) for v in value]
    if isinstance(value, dict):
        return {k: _map_strings(v, edit) for k, v in value.items()}
    return value


# --- (a) names ------------------------------------------------------------------


def library_name_findings(entries: dict[str, dict[str, Any]]) -> list[str]:
    """Every way a library record's names differ from the platform library's."""
    components = {n for n, e in entries.items() if e["kind"] == "component"}
    orchestrations = {n for n, e in entries.items() if e["kind"] == "orchestration"}
    findings = [f"extra component {n}" for n in sorted(components - LIBRARY_COMPONENTS)]
    findings += [f"missing component {n}" for n in sorted(LIBRARY_COMPONENTS - components)]
    findings += [
        f"extra orchestration {n}" for n in sorted(orchestrations - LIBRARY_ORCHESTRATIONS)
    ]
    findings += [
        f"missing orchestration {n}" for n in sorted(LIBRARY_ORCHESTRATIONS - orchestrations)
    ]
    return findings


def test_the_library_is_the_platform_library():
    assert library_name_findings(SOURCE["entries"]) == []


def test_control_4_an_extra_or_missing_name_is_a_finding():
    planted = dict(SOURCE["entries"])
    planted["json-transform"] = {"kind": "component"}
    del planted["delay-wait"]
    assert library_name_findings(planted) == [
        "extra component json-transform",
        "missing component delay-wait",
    ]


def test_the_catalog_is_the_library_plus_non_colliding_scaffolds():
    catalog = {t.name: t for t in perceivo.templates.list()}
    library = {n for n, t in catalog.items() if t.template_source_id is not None}
    assert library == LIBRARY_COMPONENTS | LIBRARY_ORCHESTRATIONS
    scaffolds = set(catalog) - library
    assert scaffolds and all(catalog[n].kind == "orchestration" for n in scaffolds)
    assert len(catalog) == len(library) + len(scaffolds)
    assert len(catalog) >= 23


def test_the_sdk_only_component_templates_are_gone():
    catalog = {t.name: t for t in perceivo.templates.list()}
    assert "json-transform" not in catalog
    # http-request is back, but as the library's master, not the SDK's own scaffold.
    assert (
        catalog["http-request"].template_source_id == SOURCE["entries"]["http-request"]["asset_id"]
    )


def test_database_connector_is_the_librarys_agent():
    template = perceivo.templates.get("database-connector")
    assert template.component_type == "AGENT"
    bundle = _catalog._library_bundle("database-connector")["component"]
    declared = {d["name"] for d in bundle["config"]["input_parameters"]["declarations"]}
    assert {"connection_mode", "platform_database_id", "operation", "custom_query"} <= declared


# --- (b) runnable ---------------------------------------------------------------


@pytest.mark.parametrize("name", ALL)
def test_every_entry_is_used_validated_and_run(name, tmp_path):
    outcome = _exercise(name, tmp_path)
    assert outcome["errors"] == []
    result = outcome["result"]
    assert result.success, result.error
    if name in SLOTS:
        filled, reached = SLOTS[name]
        assert len(outcome["stubs"]) == filled
        assert len(outcome["ran"]) >= reached
        if filled:
            assert outcome["ran"], "no slot stub appears in the run trace"


def test_the_population_is_the_whole_catalog():
    assert len(ALL) >= 23
    assert set(SLOTS) == LIBRARY_ORCHESTRATIONS


def test_harness_control_a_slot_template_does_not_run_unfilled(tmp_path):
    written = perceivo.templates.use("conditional-router", into=str(tmp_path))
    result = asyncio.run(
        run_orchestration(str(written), input_data=fit_start_input(written, {"prompt": "hi"}))
    )
    assert result.success is False
    assert "No connection found for port" in str(result.error)


def test_control_1_a_planted_removed_name_fails_the_run(tmp_path, monkeypatch):
    planted = []

    def plant(text: str) -> str:
        if not planted and "perceivo.primitives.FsmOutput" in text:
            planted.append(True)
            return text.replace("perceivo.primitives.FsmOutput", "perceivo.LayerOutput", 1)
        return text

    _with_bundle_edit(monkeypatch, "sequential-executor", plant)
    outcome = _exercise("sequential-executor", tmp_path)
    assert planted
    assert outcome["result"].success is False
    assert "LayerOutput" in str(outcome["result"].error)


def test_control_2_parallel_executor_without_rule_3_fails_the_run(tmp_path, monkeypatch):
    call = "timeout_seconds=timeout,\n            failure_strategy=failure_strategy,"
    unfixed = (
        "timeout_seconds=timeout,\n"
        '            fail_fast=failure_strategy == "fail_fast",\n'
        "            failure_strategy=failure_strategy,"
    )
    _with_bundle_edit(monkeypatch, "parallel-executor", lambda s: s.replace(call, unfixed))
    outcome = _exercise("parallel-executor", tmp_path)
    assert outcome["result"].success is False
    assert "fail_fast" in str(outcome["result"].error)


def test_control_3_error_handler_without_rule_5_fails_validation(tmp_path, monkeypatch):
    _with_bundle_edit(
        monkeypatch,
        "error-handler",
        lambda s: s.replace(vendor.RULE5_RETURN_NEW, vendor.RULE5_RETURN_OLD),
    )
    outcome = _exercise("error-handler", tmp_path)
    assert any(e.endswith("VAL-UNVERIFIABLE-RESP-001") for e in outcome["errors"]), outcome[
        "errors"
    ]


def test_control_4b_an_unrewritten_entry_fails_the_run(tmp_path, monkeypatch):
    _with_bundle_edit(
        monkeypatch, "sequential-executor", lambda s: s.replace("perceivo.primitives.", "perceivo.")
    )
    outcome = _exercise("sequential-executor", tmp_path)
    assert outcome["result"].success is False
    assert "has no attribute" in str(outcome["result"].error)


# --- (c) provenance -------------------------------------------------------------


def provenance_findings(library: Path, record: dict[str, Any]) -> list[str]:
    """Every vendored file whose bytes differ from its recorded sha256."""
    return [
        name
        for name, entry in sorted(record["entries"].items())
        if hashlib.sha256((library / entry["file"]).read_bytes()).hexdigest()
        != entry["vendored_sha256"]
    ]


def test_every_vendored_file_matches_its_recorded_hash():
    assert provenance_findings(LIBRARY, SOURCE) == []
    files = {p.name for p in LIBRARY.iterdir() if p.is_file()}
    assert files == {e["file"] for e in SOURCE["entries"].values()} | {"SOURCE.json"}


def test_control_5_one_changed_byte_is_a_finding(tmp_path):
    copy = tmp_path / "_library"
    copy.mkdir()
    for entry in SOURCE["entries"].values():
        (copy / entry["file"]).write_bytes((LIBRARY / entry["file"]).read_bytes())
    target = copy / SOURCE["entries"]["delay-wait"]["file"]
    data = bytearray(target.read_bytes())
    data[-2] = ord("X") if data[-2] != ord("X") else ord("Y")
    target.write_bytes(bytes(data))
    assert provenance_findings(copy, SOURCE) == ["delay-wait"]


def test_every_rule_5_edit_landed_in_the_vendored_error_handler():
    text = (LIBRARY / SOURCE["entries"]["error-handler"]["file"]).read_text(encoding="utf-8")
    edits = [
        (vendor.RULE5_RETURN_OLD, vendor.RULE5_RETURN_NEW),
        (vendor.RULE5_DOC_OLD, vendor.RULE5_DOC_NEW),
        *vendor.RULE5_PROSE,
    ]
    assert len(edits) == 8
    for old, new in edits:
        assert json.dumps(new)[1:-1] in text, new
        assert json.dumps(old)[1:-1] not in text, old
    primary = json.loads(text)["components"]["comp_4"]
    prose = json.dumps(
        [
            primary["description"],
            primary["manifest_yaml"],
            primary["config"]["input_parameters"]["declarations"],
            primary["script"],
        ]
    )
    for claim in ("pass data through", "straight through", "the same data", "pass-through"):
        assert claim not in prose, claim


def test_the_vendored_content_is_a_fixed_point_of_the_rewrite():
    """Re-applying every rule changes nothing: no removed name is left to rewrite."""
    for name, entry in SOURCE["entries"].items():
        data = (LIBRARY / entry["file"]).read_bytes()
        assert vendor.serialise(vendor.rewrite_bundle(json.loads(data), name)) == data, name


def test_the_recorded_asset_ids_are_the_platform_derivation():
    for name, entry in SOURCE["entries"].items():
        assert entry["asset_id"] == perceivo.templates.get(name).template_source_id
    assert SOURCE["entries"]["generic-start"]["asset_id"] == "a0000000-0000-000a-00c0-000000000011"
    assert (
        SOURCE["entries"]["sequential-executor"]["asset_id"]
        == "a0000000-0000-0006-0000-000000000001"
    )


@pytest.mark.parametrize("name", sorted(LIBRARY_COMPONENTS))
def test_a_component_copy_carries_its_library_id(name, tmp_path):
    written = perceivo.templates.use(name, into=str(tmp_path))
    manifest = yaml.safe_load(next(written.glob("*.yaml")).read_text(encoding="utf-8"))
    assert manifest["template_source_id"] == SOURCE["entries"][name]["asset_id"]


@pytest.mark.parametrize("name", sorted(LIBRARY_COMPONENTS | LIBRARY_ORCHESTRATIONS))
def test_nothing_use_writes_carries_the_system_label(name, tmp_path):
    written = perceivo.templates.use(name, into=str(tmp_path))
    labelled = [
        p.name
        for p in written.rglob("*")
        if p.is_file() and "READ-ONLY" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert labelled == []


# --- the rewrite rules ----------------------------------------------------------


def test_rule_1_and_2_move_root_classes_under_primitives():
    text = "perceivo.AgentOutput(artifact=perceivo.Artifact(type=perceivo.ArtifactType.JSON))"
    assert vendor.rewrite_text(text, "x") == (
        "perceivo.primitives.AgentOutput(artifact=perceivo.primitives.Artifact("
        "type=perceivo.primitives.ArtifactType.JSON))"
    )
    assert vendor.rewrite_text("perceivo.LayerOutput", "x") == "perceivo.primitives.FsmOutput"
    assert vendor.rewrite_text("@perceivo.agent", "x") == "@perceivo.agent"


def test_rule_3_is_scoped_to_parallel_executor():
    text = '\n    fail_fast = failure_strategy == "fail_fast"\n    x(\n        fail_fast=fail_fast,\n    )'
    assert "fail_fast=" not in vendor.rewrite_text(text, "parallel-executor")
    assert vendor.rewrite_text(text, "sequential-executor") == text


def test_rule_5_is_scoped_to_error_handler_and_fixes_every_primary_claim():
    edits = [
        (vendor.RULE5_RETURN_OLD, vendor.RULE5_RETURN_NEW),
        (vendor.RULE5_DOC_OLD, vendor.RULE5_DOC_NEW),
        *vendor.RULE5_PROSE,
    ]
    text = "\n".join(old for old, _ in edits)
    out = vendor.rewrite_text(text, "error-handler")
    assert out == "\n".join(new for _, new in edits)
    assert "forwards" not in out
    assert "pass-through" not in out
    assert vendor.rewrite_text(text, "parallel-executor") == text


def test_rule_6_drops_private_ticket_references():
    # Spelled in two pieces so this file itself carries no ticket identifier.
    marker = "TI" + "CK-999"
    assert vendor.rewrite_text(f"# Fail-closed ({marker}): x", "x") == "# Fail-closed: x"
