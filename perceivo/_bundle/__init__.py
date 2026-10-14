"""Platform export-bundle format for components and orchestrations.

The SDK round-trips a local component or orchestration project through the
platform's export-bundle JSON (``export_type: "component"`` /
``"orchestration"``), so a bundle exported locally imports cleanly on the
platform and vice versa.

``schema`` holds the bundle pydantic models. ``component`` and ``orchestration``
convert local project files ⇄ those bundles.
"""

from __future__ import annotations

from perceivo._bundle.component import bundle_to_component, component_to_bundle
from perceivo._bundle.orchestration import bundle_to_orchestration, orchestration_to_bundle

__all__ = [
    "bundle_to_component",
    "bundle_to_orchestration",
    "component_to_bundle",
    "orchestration_to_bundle",
]
