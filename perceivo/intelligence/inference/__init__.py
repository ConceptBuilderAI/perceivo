"""Real-time predictions and the log of past ones.

``predict`` asks a deployed model a question now; ``predictions`` reads back
the questions it has already answered. Prediction calls are routed through the
egress broker and require the ``INTELLIGENCE_PREDICT`` privilege.

Locally, ``predict`` is served by a deterministic, seedable inference mock
whose calls are recorded in the mock trace.
"""

from __future__ import annotations

from ._client import predict, predictions

__all__ = ["predict", "predictions"]
