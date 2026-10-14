"""Unit tests for the three public ML planes.

``perceivo.intelligence.features`` (4 verbs), ``perceivo.capabilities`` (3) and
``perceivo.intelligence.inference.predictions`` (1) replace a set of verbs that
addressed storage paths and a generic ``data_fetch(path)``. What is pinned here
is the seam between the SDK and the runtime -- the call the plane makes on the
extension manager, and what it does with the answer -- because that seam is a
``getattr`` on an object the SDK never imports and no type checker crosses it.

Four properties get most of the attention:

1. **Absence is reported, never defaulted.** ``capabilities.graph`` and
   ``inference.predictions`` raise ``NotFoundError`` when the runtime answers
   ``None``. The verb ``predictions`` replaces returned ``[]`` on a 404, and
   three seeded components ran a synthetic-sample fallback for their entire
   history on the strength of that empty list. ``[]`` and ``None`` are
   different answers and the tests below distinguish them.
2. **No ``organization_id``, anywhere.** Every backing route is
   organization-scoped and the runtime fills it from the execution context.
   A component that could pass one could read another organization's data,
   and the broker would authorize it -- ``files:read`` says the caller may
   read files, not whose. Asserted against the signatures, not by inspection.
3. **The wire shape is the record shape.** ``_from_wire`` faults on a missing
   key rather than defaulting it, so each mapping verb is tested against a
   payload that omits one.
4. **Row dicts stay dicts.** ``get_records`` returns whatever ingest wrote.
   The SDK does not own that shape and does not invent a record for it.
"""

from __future__ import annotations

import inspect

import pytest

from perceivo import capabilities
from perceivo.capabilities._types import Capability, ExecutionGraph, ExecutionGraphSummary
from perceivo.intelligence import features, inference
from perceivo.intelligence._types import FeatureView, Materialization, PredictionRecord
from perceivo.types.errors import NotFoundError, ServiceError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _recorder(result):
    """An async stand-in recording how it was called, and returning *result*.

    Positional and keyword arguments are recorded separately because the seam
    is not uniformly keyword-called: ``capabilities.graphs`` passes its one
    argument positionally and everything else passes by name. A recorder that
    normalized the two would not notice that changing.
    """
    seen: dict = {}
    pos: list = []

    async def fn(*args, **kwargs):
        pos.extend(args)
        seen.update(kwargs)
        return result

    fn.seen = seen
    fn.pos = pos
    return fn


_GRAPH = {
    "graph_id": "g-1",
    "capability_id": "c-1",
    "graph_type": "full_run",
    "config": {"window": 7},
    "created_at": "2026-08-20T00:00:00Z",
    "summary": {"total_entries": 2},
    "orchestrations": {"o-1": {"name": "nightly"}},
    "entries": [{"step": 1}, {"step": 2}],
}

_PREDICTION = {
    "prediction_id": "p-1",
    "intelligence_id": "i-1",
    "model_id": "m-1",
    "family_id": None,
    "entity_id": "e-1",
    "correlation_id": None,
    "request_id": None,
    "served_by": "champion",
    "timestamp": "2026-08-20T00:00:00Z",
    "latency_ms": 12,
    "input_data": {"x": 1},
    "prediction_result": [0.9],
    "is_error": False,
    "error_message": None,
}


# ---------------------------------------------------------------------------
# features.get_records / get_online_records
# ---------------------------------------------------------------------------


async def test_get_records_returns_the_rows_ingest_wrote(stub_ext):
    """No record type: a feature view's row shape belongs to the view.

    The platform does not own it -- a view is whatever the ingest task wrote --
    so a record here would be a shape the SDK invented for data it has never
    seen.
    """
    rows = [{"user_id": "u1", "clicks": 3}, {"user_id": "u2", "clicks": 0}]
    stub_ext.get_feature_records = _recorder(rows)

    out = await features.get_records("user_events", limit=10)

    assert out == rows
    assert out[0] is rows[0]
    assert stub_ext.get_feature_records.seen == {
        "feature_view": "user_events",
        "limit": 10,
    }


async def test_get_records_defaults_to_no_limit(stub_ext):
    stub_ext.get_feature_records = _recorder([])
    await features.get_records("user_events")
    assert stub_ext.get_feature_records.seen["limit"] is None


async def test_get_online_records_forwards_entity_ids(stub_ext):
    """Passing ids is the fast path; ``None`` scans. Both must reach the runtime."""
    stub_ext.get_online_feature_records = _recorder([{"user_id": "u1"}])

    await features.get_online_records("user_events", entity_ids=["u1"], limit=5)

    assert stub_ext.get_online_feature_records.seen == {
        "feature_view": "user_events",
        "entity_ids": ["u1"],
        "limit": 5,
    }


async def test_get_online_records_defaults_scan_the_view(stub_ext):
    stub_ext.get_online_feature_records = _recorder([])
    await features.get_online_records("user_events")
    seen = stub_ext.get_online_feature_records.seen
    assert seen["entity_ids"] is None
    assert seen["limit"] is None


# ---------------------------------------------------------------------------
# features.list_views
# ---------------------------------------------------------------------------


async def test_list_views_types_every_row(stub_ext):
    stub_ext.list_feature_views = _recorder(
        [{"name": "user_events", "version": 3}, {"name": "sessions", "version": 1}]
    )

    out = await features.list_views()

    assert [(v.name, v.version) for v in out] == [("user_events", 3), ("sessions", 1)]
    assert all(isinstance(v, FeatureView) for v in out)


async def test_list_views_faults_on_a_view_without_a_version(stub_ext):
    """``version`` is required because the backing call cannot omit it.

    ``list_feature_views_with_versions`` returns ``dict[str, int]``, so both
    fields exist by construction. A response missing one is the service
    disagreeing with its own contract, and defaulting it would hand a component
    a version nobody sent -- which it would then read features at.
    """
    stub_ext.list_feature_views = _recorder([{"name": "user_events"}])

    with pytest.raises(ServiceError):
        await features.list_views()


async def test_list_views_returns_empty_for_a_store_with_no_views(stub_ext):
    stub_ext.list_feature_views = _recorder([])
    assert await features.list_views() == []


# ---------------------------------------------------------------------------
# features.materialize
# ---------------------------------------------------------------------------


async def test_materialize_forwards_the_window_and_types_the_result(stub_ext):
    stub_ext.materialize_features = _recorder(
        {
            "status": "completed",
            "feature_views": ["user_events"],
            "keys_written": 412,
        }
    )

    out = await features.materialize(["user_events"], start_date="2026-01-01")

    assert isinstance(out, Materialization)
    assert (out.status, out.keys_written) == ("completed", 412)
    assert out.message is None, "message defaults; the success path does not build it"
    assert stub_ext.materialize_features.seen == {
        "feature_views": ["user_events"],
        "start_date": "2026-01-01",
        "end_date": None,
    }


async def test_materialize_carries_the_nothing_requested_message(stub_ext):
    """The service's two returns differ by one key, and the extra one is the
    only signal that nothing happened -- ``keys_written`` is 0 either way."""
    msg = "No feature views specified. Only explicitly requested views are materialized."
    stub_ext.materialize_features = _recorder(
        {
            "status": "completed",
            "feature_views": [],
            "keys_written": 0,
            "message": msg,
        }
    )

    out = await features.materialize([])

    assert (out.status, out.keys_written, out.message) == ("completed", 0, msg)


def test_materialize_has_no_materialize_everything_call():
    """``feature_views`` is required, so the empty case is typed, not implicit.

    The service accepts an empty list and answers with a no-op. Requiring the
    argument turns a silent no-op into a signature error at authoring time.
    """
    sig = inspect.signature(features.materialize)
    assert sig.parameters["feature_views"].default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


async def test_list_capabilities_types_every_row(stub_ext):
    stub_ext.list_capabilities = _recorder(
        [{"id": "c-1", "name": "alpha", "organization_id": "o-1"}]
    )

    out = await capabilities.list()

    assert isinstance(out[0], Capability)
    assert (out[0].id, out[0].name, out[0].organization_id) == ("c-1", "alpha", "o-1")


async def test_graphs_tolerates_the_two_fields_blob_metadata_may_omit(stub_ext):
    """``size_bytes`` and ``created_at`` come from blob metadata, not the route.

    They are declared optional for that reason and must not fault -- unlike
    ``graph_id``, which the route parses from the filename every time.
    """
    stub_ext.list_execution_graphs = _recorder(
        [
            {
                "graph_id": "g-1",
                "capability_id": "c-1",
                "blob_path": "caps/c-1/g-1.json",
                "size_bytes": None,
                "created_at": None,
            }
        ]
    )

    out = await capabilities.graphs("c-1")

    assert isinstance(out[0], ExecutionGraphSummary)
    assert out[0].size_bytes is None
    assert out[0].created_at is None
    assert (stub_ext.list_execution_graphs.pos, stub_ext.list_execution_graphs.seen) == (
        ["c-1"],
        {},
    )


async def test_graph_reads_one_graph_whole(stub_ext):
    stub_ext.get_execution_graph = _recorder(_GRAPH)

    out = await capabilities.graph("g-1", capability_id="c-1")

    assert isinstance(out, ExecutionGraph)
    assert out.graph_type == "full_run"
    assert out.entries == [{"step": 1}, {"step": 2}]
    assert stub_ext.get_execution_graph.seen == {
        "graph_id": "g-1",
        "capability_id": "c-1",
    }


async def test_graph_raises_rather_than_returning_a_hollow_graph(stub_ext):
    """The runtime reports absence as ``None``; the translation happens in the SDK.

    That split is deliberate: the feature service never has to import the SDK's
    exception vocabulary to say "missing".
    """
    stub_ext.get_execution_graph = _recorder(None)

    with pytest.raises(NotFoundError):
        await capabilities.graph("g-nope", capability_id="c-1")


def test_graph_takes_capability_id_by_keyword_only():
    """Both ids are UUID strings, so a positional swap would 404 rather than fail."""
    kind = inspect.signature(capabilities.graph).parameters["capability_id"].kind
    assert kind is inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# inference.predictions
# ---------------------------------------------------------------------------


async def test_predictions_forwards_every_selector(stub_ext):
    stub_ext.list_predictions = _recorder([_PREDICTION])

    out = await inference.predictions(
        "i-1",
        start="2026-08-01",
        end="2026-08-20",
        correlation_id="corr",
        request_id="req",
        entity_id="e-1",
        model_id="m-1",
        limit=50,
    )

    assert isinstance(out[0], PredictionRecord)
    assert (out[0].prediction_id, out[0].is_error) == ("p-1", False)
    assert stub_ext.list_predictions.seen == {
        "intelligence_id": "i-1",
        "start": "2026-08-01",
        "end": "2026-08-20",
        "correlation_id": "corr",
        "request_id": "req",
        "entity_id": "e-1",
        "model_id": "m-1",
        "limit": 50,
    }


async def test_predictions_treats_an_empty_window_as_an_empty_window(stub_ext):
    """A quiet week is a real answer and must not be confused with the next test."""
    stub_ext.list_predictions = _recorder([])
    assert await inference.predictions("i-1") == []


async def test_predictions_raises_for_an_intelligence_that_is_not_there(stub_ext):
    """The defect this replaces, stated as a test.

    The vendored fork swallowed its 404 and returned ``[]``. Three seeded
    components read that as "no predictions yet" and ran their synthetic-sample
    fallback for their entire history. ``None`` from the runtime -- a missing,
    deleted, or foreign intelligence, which are one answer on the wire -- has
    to be distinguishable from an empty window here or it is distinguishable
    nowhere.
    """
    stub_ext.list_predictions = _recorder(None)

    with pytest.raises(NotFoundError):
        await inference.predictions("i-nope")


async def test_predictions_omitted_id_reaches_the_runtime_as_none(stub_ext):
    """The default is the service's to fill, not the SDK's to guess.

    A component reading back its own log cannot name itself -- the executing
    intelligence id lives on the execution context and no verb here returns it.
    So the omitted argument has to arrive at the runtime as ``None``: the
    ExtensionManager holds the id and substitutes it. Anything the SDK invented
    here would be a different intelligence than the one running.
    """
    stub_ext.list_predictions = _recorder([_PREDICTION])

    out = await inference.predictions(limit=5)

    assert isinstance(out[0], PredictionRecord)
    assert stub_ext.list_predictions.seen["intelligence_id"] is None
    assert stub_ext.list_predictions.seen["limit"] == 5


async def test_predictions_named_id_still_wins(stub_ext):
    """Optional is not ignored -- an explicit id crosses unchanged."""
    stub_ext.list_predictions = _recorder([])

    assert await inference.predictions("i-other") == []
    assert stub_ext.list_predictions.seen["intelligence_id"] == "i-other"


async def test_predictions_absent_id_does_not_report_none_as_the_subject(stub_ext):
    """``f"...{intelligence_id}"`` would put the word "None" in front of an operator.

    The id the service substituted is not knowable on this side, so the message
    names the relationship instead of a value. A component that hits this is
    running under an intelligence that has just been deleted, and "Intelligence
    not found: None" would send whoever reads it looking for a bug in the SDK.
    """
    stub_ext.list_predictions = _recorder(None)

    with pytest.raises(NotFoundError) as excinfo:
        await inference.predictions()

    assert "None" not in str(excinfo.value)
    assert "executing intelligence" in str(excinfo.value)


async def test_predictions_faults_on_a_record_missing_a_projected_key(stub_ext):
    """``_project`` builds all fourteen keys unconditionally, so absence is drift."""
    partial = dict(_PREDICTION)
    del partial["is_error"]
    stub_ext.list_predictions = _recorder([partial])

    with pytest.raises(ServiceError):
        await inference.predictions("i-1")


# ---------------------------------------------------------------------------
# cross-cutting
# ---------------------------------------------------------------------------


NEW_VERBS = [
    features.get_records,
    features.get_online_records,
    features.list_views,
    features.materialize,
    capabilities.list,
    capabilities.graphs,
    capabilities.graph,
    inference.predictions,
]


@pytest.mark.parametrize("verb", NEW_VERBS, ids=lambda v: f"{v.__module__}.{v.__name__}")
def test_no_verb_accepts_an_organization(verb):
    """The organization is the execution context's to supply, never the caller's.

    Every backing route is organization-scoped. A parameter here would let a
    component name an organization it does not belong to, and the broker would
    authorize the call -- the capability grants the verb, not the tenant.
    """
    assert "organization_id" not in inspect.signature(verb).parameters


@pytest.mark.parametrize("verb", NEW_VERBS, ids=lambda v: f"{v.__module__}.{v.__name__}")
async def test_every_verb_refuses_outside_component_execution(verb, cleared_context):
    """``get_ext()`` raises rather than returning a no-op manager.

    A plane that degraded to a default outside a component would let a broken
    runtime look like an empty result set.
    """
    args = [] if verb in (features.list_views, capabilities.list) else ["x"]
    kwargs = {"capability_id": "c-1"} if verb is capabilities.graph else {}
    with pytest.raises(RuntimeError):
        await verb(*args, **kwargs)
