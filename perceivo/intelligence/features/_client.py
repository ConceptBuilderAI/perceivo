"""Feature-store verbs, backed by the feature service's runtime.

These verbs run only inside a feature service extension, which owns the
offline store, the online store and the Feast service. Called from a training,
inference or evaluation runtime they raise ``AttributeError`` naming the
missing method.

There are no raw blob-path verbs: the storage layout is the platform's and
changes without notice. Reads are addressed by feature view.
"""

from __future__ import annotations

from typing import Any, cast

from ..._internal.context import _ExecutionContext
from ...types.records import _from_wire
from .._types import FeatureView, Materialization


async def get_records(feature_view: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Read rows from a feature view in the offline store.

    The latest version of the view is read, denormalized if it was written in
    the normalized layout, and returned as plain row dicts. Row shape is the
    view's, not the platform's (a feature view is whatever the ingest task
    wrote), so these stay ``dict`` rather than becoming a record type this
    module would have to invent.

    Args:
        feature_view: Name of the view, as ingest wrote it. Discover the
            available names with :func:`list_views`.
        limit: Return at most this many rows. ``None`` returns all of them.

    Returns:
        Row dicts, oldest-first as stored. Empty when the view has no versions
        yet, which is a real state, not an error: ingest creates the view on
        its first write.

    Raises:
        RuntimeError: If called outside component execution context.
        AttributeError: Outside a feature service extension.

    Example::

        import perceivo

        class Ingest(perceivo.intelligence.FeatureComponent):
            async def prepare_data(self, input):
                self._rows = await perceivo.intelligence.features.get_records(
                    "user_events", limit=1000
                )
                return perceivo.types.IngestPlan(total_items=len(self._rows))
    """
    ext = _ExecutionContext.get_ext()
    return cast(
        list[dict[str, Any]], await ext.get_feature_records(feature_view=feature_view, limit=limit)
    )


async def get_online_records(
    feature_view: str,
    entity_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read rows from a feature view in the **online** store.

    The online store is Redis and holds only what :func:`materialize` has
    copied there, so this and :func:`get_records` can legitimately disagree:
    the offline store is the record, the online store is the serving copy.

    Args:
        feature_view: Name of the view.
        entity_ids: Fetch exactly these entities. ``None`` scans the view,
            which is the slower path; pass ids when you have them.
        limit: Return at most this many rows.

    Returns:
        Row dicts. Empty when the view has never been materialized, which is
        indistinguishable from a materialized-but-empty view; if that
        distinction matters, call :func:`materialize` and read its
        ``keys_written``.

    Raises:
        RuntimeError: If called outside component execution context.
        AttributeError: Outside a feature service extension.
        ValueError: If the intelligence has the online store disabled. The
            store is optional per intelligence, and a component that assumed
            otherwise should hear so rather than read an empty list.
    """
    ext = _ExecutionContext.get_ext()
    return cast(
        list[dict[str, Any]],
        await ext.get_online_feature_records(
            feature_view=feature_view, entity_ids=entity_ids, limit=limit
        ),
    )


async def list_views() -> list[FeatureView]:
    """List the feature views in the offline store with their versions.

    Returns:
        One :class:`~perceivo.intelligence._types.FeatureView` per view,
        name-ordered. A view with no data yet does not appear.

    Raises:
        RuntimeError: If called outside component execution context.
        AttributeError: Outside a feature service extension.
    """
    ext = _ExecutionContext.get_ext()
    return [_from_wire(FeatureView, v) for v in await ext.list_feature_views()]


async def materialize(
    feature_views: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
) -> Materialization:
    """Copy offline features into the online store so inference can serve them.

    Only the views named are materialized; there is no "materialize
    everything" call. ``feature_views`` is required so that omitting it is a
    signature error rather than a silent no-op.

    A view whose parquet is missing is skipped rather than raising, so a
    partial result reports a real ``keys_written`` and names every view it was
    asked for. Compare ``keys_written`` against what you expected if partial
    success matters.

    Args:
        feature_views: Views to materialize. Must be non-empty to do anything.
        start_date: Keep only rows at or after this timestamp. ``None`` keeps
            everything.
        end_date: Keep only rows at or before this timestamp.

    Returns:
        A :class:`~perceivo.intelligence._types.Materialization`.

    Raises:
        RuntimeError: If called outside component execution context, or if the
            Feast service has not finished initializing.
        AttributeError: Outside a feature service extension.
        ValueError: If the online store is disabled for this intelligence, or
            Redis is unreachable. Both are configuration faults, not empty
            results.

    Example::

        result = await perceivo.intelligence.features.materialize(
            ["user_events"], start_date="2026-01-01"
        )
        perceivo.log.info(f"served {result.keys_written} keys")
    """
    ext = _ExecutionContext.get_ext()
    return _from_wire(
        Materialization,
        await ext.materialize_features(
            feature_views=feature_views,
            start_date=start_date,
            end_date=end_date,
        ),
    )
