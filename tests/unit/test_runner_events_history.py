"""T6: Runner events + run history."""

from __future__ import annotations

import json
import time

from perceivo.runner.events import EventType, ExecutionEvent
from perceivo.runner.history import RunHistory


def test_event_factory_methods_set_type():
    cases = [
        (ExecutionEvent.node_enter("n", "AGENT"), EventType.NODE_ENTER),
        (ExecutionEvent.node_exit("n", "AGENT", 12.5), EventType.NODE_EXIT),
        (ExecutionEvent.log("hi"), EventType.LOG),
        (ExecutionEvent.token("tok"), EventType.TOKEN),
        (ExecutionEvent.cost(0.001, "openai"), EventType.COST),
        (ExecutionEvent.error("boom"), EventType.ERROR),
        (ExecutionEvent.complete(True), EventType.COMPLETE),
    ]
    for ev, et in cases:
        assert ev.event_type is et


def test_event_to_dict_serializes_enum_as_string():
    ev = ExecutionEvent.node_enter("n", "AGENT")
    d = ev.to_dict()
    assert d["event_type"] == "node_enter"
    assert d["node_name"] == "n"


def test_event_to_json_roundtrips():
    ev = ExecutionEvent.log("msg", level="WARN", node_name="x")
    payload = json.loads(ev.to_json())
    assert payload["event_type"] == "log"
    assert payload["data"]["level"] == "WARN"
    assert payload["node_name"] == "x"


def test_run_history_writes_one_record_on_completion(tmp_workspace, isolated_perceivo_home):
    hist = RunHistory(str(tmp_workspace))
    rec = hist.start_run("flow", input_data={"x": 1})
    assert rec.run_id
    assert rec.input_data == {"x": 1}
    assert rec.completed_at is None

    # Nothing is written until the run completes, and then exactly one record.
    runs_dir = isolated_perceivo_home / "runs"
    assert not runs_dir.exists()
    hist.complete_run(rec, success=True)
    assert len(list(runs_dir.glob("*.json"))) == 1


def test_run_history_complete_writes_success_and_output(tmp_workspace):
    hist = RunHistory(str(tmp_workspace))
    rec = hist.start_run("flow")
    hist.complete_run(rec, success=True, output_data={"y": 2})

    loaded = hist.get_run(rec.run_id)
    assert loaded.success is True
    assert loaded.output_data == {"y": 2}
    assert loaded.completed_at is not None


def test_run_history_add_event_records_dict(tmp_workspace):
    hist = RunHistory(str(tmp_workspace))
    rec = hist.start_run("flow")
    ev = ExecutionEvent.node_enter("a", "AGENT")
    hist.add_event(rec, ev)
    assert rec.events
    assert rec.events[0]["event_type"] == "node_enter"


def test_run_history_list_runs_newest_first(tmp_workspace):
    hist = RunHistory(str(tmp_workspace))
    r1 = hist.start_run("first")
    hist.complete_run(r1)
    time.sleep(0.01)
    r2 = hist.start_run("second")
    hist.complete_run(r2)

    runs = hist.list_runs(limit=10)
    assert [r["run_id"] for r in runs[:2]] == [r2.run_id, r1.run_id]


def test_run_history_list_runs_respects_limit(tmp_workspace):
    hist = RunHistory(str(tmp_workspace))
    for i in range(5):
        hist.complete_run(hist.start_run(f"r{i}"))
    assert len(hist.list_runs(limit=3)) == 3


def test_run_history_get_missing_run_returns_none(tmp_workspace):
    hist = RunHistory(str(tmp_workspace))
    assert hist.get_run("nope") is None
