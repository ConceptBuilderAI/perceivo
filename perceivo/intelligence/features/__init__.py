"""Read and materialize feature views from the feature store.

The plane is view-oriented: read a feature view, list the views, push a view
to the online store. Each verb dispatches to the feature service runtime;
outside that runtime the call raises ``AttributeError``.
"""

from __future__ import annotations

from .._types import FeatureView, Materialization
from ._client import get_online_records, get_records, list_views, materialize

__all__ = [
    "FeatureView",
    "Materialization",
    "get_online_records",
    "get_records",
    "list_views",
    "materialize",
]
