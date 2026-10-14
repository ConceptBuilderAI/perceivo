"""Capabilities and their execution history.

Three typed verbs: ``list`` the organization's capabilities, ``graphs`` for a
capability's stored execution graphs (``ExecutionGraphSummary``, no download),
and ``graph`` for one full graph. None of them takes a route or an
organization id: the platform supplies both.
"""

from __future__ import annotations

from ._client import graph, graphs, list
from ._types import Capability, ExecutionGraph, ExecutionGraphSummary

__all__ = [
    "Capability",
    "ExecutionGraph",
    "ExecutionGraphSummary",
    "graph",
    "graphs",
    "list",
]
