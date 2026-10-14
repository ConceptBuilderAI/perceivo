"""Cost recording for the perceivo SDK.

Records costs from within component execution. Costs are stored in the platform
and appear in orchestration/capability cost reports.

Usage:
    import perceivo

    # Record a custom cost
    await perceivo.cost.record("External API call", 0.05)

    # Record with metadata
    await perceivo.cost.record(
        "Geocoding API", 0.001, metadata={"provider": "google", "calls": 10}
    )

The server does not enforce the ``custom:`` prefix on a cost type.
"""

from ._recorder import model_pricing, record

__all__ = [
    "model_pricing",
    "record",
]
